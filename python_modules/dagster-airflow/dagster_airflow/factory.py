import datetime
import re

from airflow import DAG
from airflow.operators import BaseOperator

from dagster import ExecutionTargetHandle, RunConfig, check, seven
from dagster.core.execution.api import create_execution_plan
from dagster.core.instance import DagsterInstance

from .compile import coalesce_execution_steps
from .operators.docker_operator import DagsterDockerOperator
from .operators.python_operator import DagsterPythonOperator

DEFAULT_ARGS = {
    'depends_on_past': False,
    'email': ['airflow@example.com'],
    'email_on_failure': False,
    'email_on_retry': False,
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': datetime.timedelta(0, 300),
    'start_date': datetime.datetime(1900, 1, 1, 0, 0),
}

# Airflow DAG names are not allowed to be longer than 250 chars
AIRFLOW_MAX_DAG_NAME_LEN = 250


def _make_dag_description(pipeline_name):
    return '''Editable scaffolding autogenerated by dagster-airflow from pipeline {pipeline_name}
    '''.format(
        pipeline_name=pipeline_name
    )


def _rename_for_airflow(name):
    '''Modify pipeline name for Airflow to meet constraints on DAG names:
    https://github.com/apache/airflow/blob/1.10.3/airflow/utils/helpers.py#L52-L63

    Here, we just substitute underscores for illegal characters to avoid imposing Airflow's
    constraints on our naming schemes.
    '''
    return re.sub(r'[^\w\-\.]', '_', name)[:AIRFLOW_MAX_DAG_NAME_LEN]


def _make_airflow_dag(
    handle,
    pipeline_name,
    environment_dict=None,
    mode=None,
    instance=None,
    dag_id=None,
    dag_description=None,
    dag_kwargs=None,
    op_kwargs=None,
    operator=DagsterPythonOperator,
):
    check.inst_param(handle, 'handle', ExecutionTargetHandle)
    check.str_param(pipeline_name, 'pipeline_name')
    environment_dict = check.opt_dict_param(
        environment_dict, 'environment_dict', key_type=check.string_types
    )
    mode = check.opt_str_param(mode, 'mode')
    # Default to use the (persistent) system temp directory rather than a seven.TemporaryDirectory,
    # which would not be consistent between Airflow task invocations.
    instance = (
        check.inst_param(instance, 'instance', DagsterInstance)
        if instance
        else DagsterInstance.get(fallback_storage=seven.get_system_temp_directory())
    )

    # Only used for Airflow; internally we continue to use pipeline.name
    dag_id = check.opt_str_param(dag_id, 'dag_id', _rename_for_airflow(pipeline_name))

    dag_description = check.opt_str_param(
        dag_description, 'dag_description', _make_dag_description(pipeline_name)
    )
    check.subclass_param(operator, 'operator', BaseOperator)

    dag_kwargs = dict(
        {'default_args': DEFAULT_ARGS},
        **check.opt_dict_param(dag_kwargs, 'dag_kwargs', key_type=check.string_types)
    )

    op_kwargs = check.opt_dict_param(op_kwargs, 'op_kwargs', key_type=check.string_types)

    dag = DAG(dag_id=dag_id, description=dag_description, **dag_kwargs)

    pipeline = handle.build_pipeline_definition()

    if mode is None:
        mode = pipeline.get_default_mode_name()

    execution_plan = create_execution_plan(
        pipeline, environment_dict, run_config=RunConfig(mode=mode)
    )

    tasks = {}

    coalesced_plan = coalesce_execution_steps(execution_plan)

    for solid_handle, solid_steps in coalesced_plan.items():

        step_keys = [step.key for step in solid_steps]

        if operator == DagsterPythonOperator:
            task = operator(
                handle=handle,
                pipeline_name=pipeline_name,
                environment_dict=environment_dict,
                mode=mode,
                task_id=solid_handle,
                step_keys=step_keys,
                dag=dag,
                instance_ref=instance.get_ref(),
                **op_kwargs
            )
        else:
            task = operator(
                pipeline_name=pipeline_name,
                environment_dict=environment_dict,
                mode=mode,
                task_id=solid_handle,
                step_keys=step_keys,
                dag=dag,
                instance_ref=instance.get_ref(),
                **op_kwargs
            )

        tasks[solid_handle] = task

        for solid_step in solid_steps:
            for step_input in solid_step.step_inputs:
                for key in step_input.dependency_keys:
                    prev_solid_handle = execution_plan.get_step_by_key(key).solid_handle.to_string()
                    if solid_handle != prev_solid_handle:
                        tasks[prev_solid_handle].set_downstream(task)

    return (dag, [tasks[solid_handle] for solid_handle in coalesced_plan.keys()])


def make_airflow_dag(
    module_name,
    pipeline_name,
    environment_dict=None,
    mode=None,
    instance=None,
    dag_id=None,
    dag_description=None,
    dag_kwargs=None,
    op_kwargs=None,
):
    '''Construct an Airflow DAG corresponding to a given Dagster pipeline.

    Tasks in the resulting DAG will execute the Dagster logic they encapsulate as a Python
    callable, run by an underlying :py:class:`PythonOperator <airflow:PythonOperator>`. As a
    consequence, both dagster, any Python dependencies required by your solid logic, and the module
    containing your pipeline definition must be available in the Python environment within which
    your Airflow tasks execute. If you cannot install requirements into this environment, or you
    are looking for a containerized solution to provide better isolation, see instead
    :py:func:`make_airflow_dag_containerized`.

    This function should be invoked in an Airflow DAG definition file, such as that created by an
    invocation of the dagster-airflow scaffold CLI tool.

    Args:
        module_name (str): The name of the importable module in which the pipeline definition can be
            found.
        pipeline_name (str): The name of the pipeline definition.
        environment_dict (Optional[dict]): The environment config, if any, with which to compile
            the pipeline to an execution plan, as a Python dict.
        mode (Optional[str]): The mode in which to execute the pipeline.
        instance (Optional[DagsterInstance]): The Dagster instance to use to execute the pipeline.
        dag_id (Optional[str]): The id to use for the compiled Airflow DAG (passed through to
            :py:class:`DAG <airflow:airflow.models.DAG>`).
        dag_description (Optional[str]): The description to use for the compiled Airflow DAG
            (passed through to :py:class:`DAG <airflow:airflow.models.DAG>`)
        dag_kwargs (Optional[dict]): Any additional kwargs to pass to the Airflow
            :py:class:`DAG <airflow:airflow.models.DAG>` constructor, including ``default_args``.
        op_kwargs (Optional[dict]): Any additional kwargs to pass to the underlying Airflow
            operator (a subclass of
            :py:class:`PythonOperator <airflow:airflow.operators.python_operator.PythonOperator>`).

    Returns:
        (airflow.models.DAG, List[airflow.models.BaseOperator]): The generated Airflow DAG, and a
        list of its constituent tasks.

    '''
    check.str_param(module_name, 'module_name')

    handle = ExecutionTargetHandle.for_pipeline_module(module_name, pipeline_name)

    return _make_airflow_dag(
        handle=handle,
        pipeline_name=pipeline_name,
        environment_dict=environment_dict,
        mode=mode,
        instance=instance,
        dag_id=dag_id,
        dag_description=dag_description,
        dag_kwargs=dag_kwargs,
        op_kwargs=op_kwargs,
    )


def make_airflow_dag_for_handle(
    handle,
    pipeline_name,
    environment_dict=None,
    mode=None,
    dag_id=None,
    dag_description=None,
    dag_kwargs=None,
    op_kwargs=None,
):
    return _make_airflow_dag(
        handle=handle,
        pipeline_name=pipeline_name,
        environment_dict=environment_dict,
        mode=mode,
        dag_id=dag_id,
        dag_description=dag_description,
        dag_kwargs=dag_kwargs,
        op_kwargs=op_kwargs,
    )


def make_airflow_dag_containerized(
    module_name,
    pipeline_name,
    image,
    environment_dict=None,
    mode=None,
    dag_id=None,
    dag_description=None,
    dag_kwargs=None,
    op_kwargs=None,
):
    '''Construct a containerized Airflow DAG corresponding to a given Dagster pipeline.

    Tasks in the resulting DAG will execute the Dagster logic they encapsulate by calling the
    dagster-graphql API exposed by a container run using a subclass of
    :py:class:`DockerOperator <airflow:airflow.operators.docker_operator.DockerOperator>`. As a
    consequence, both dagster, any Python dependencies required by your solid logic, and the module
    containing your pipeline definition must be available in the container spun up by this operator.
    Typically you'll want to install these requirements onto the image you're using.

    This function should be invoked in an Airflow DAG definition file, such as that created by an
    invocation of the dagster-airflow scaffold CLI tool.

    Args:
        module_name (str): The name of the importable module in which the pipeline definition can be
            found.
        pipeline_name (str): The name of the pipeline definition.
        image (str): The name of the Docker image to use for execution (passed through to
            :py:class:`DockerOperator <airflow:airflow.operators.docker_operator.DockerOperator>`).
        environment_dict (Optional[dict]): The environment config, if any, with which to compile
            the pipeline to an execution plan, as a Python dict.
        mode (Optional[str]): The mode in which to execute the pipeline.
        instance (Optional[DagsterInstance]): The Dagster instance to use to execute the pipeline.
        dag_id (Optional[str]): The id to use for the compiled Airflow DAG (passed through to
            :py:class:`DAG <airflow:airflow.models.DAG>`).
        dag_description (Optional[str]): The description to use for the compiled Airflow DAG
            (passed through to :py:class:`DAG <airflow:airflow.models.DAG>`)
        dag_kwargs (Optional[dict]): Any additional kwargs to pass to the Airflow
            :py:class:`DAG <airflow:airflow.models.DAG>` constructor, including ``default_args``.
        op_kwargs (Optional[dict]): Any additional kwargs to pass to the underlying Airflow
            operator (a subclass of
            :py:class:`DockerOperator <airflow:airflow.operators.docker_operator.DockerOperator>`).

    Returns:
        (airflow.models.DAG, List[airflow.models.BaseOperator]): The generated Airflow DAG, and a
        list of its constituent tasks.
    '''
    check.str_param(module_name, 'module_name')

    handle = ExecutionTargetHandle.for_pipeline_module(module_name, pipeline_name)

    op_kwargs = check.opt_dict_param(op_kwargs, 'op_kwargs', key_type=check.string_types)
    op_kwargs['image'] = image
    return _make_airflow_dag(
        handle=handle,
        pipeline_name=pipeline_name,
        environment_dict=environment_dict,
        mode=mode,
        dag_id=dag_id,
        dag_description=dag_description,
        dag_kwargs=dag_kwargs,
        op_kwargs=op_kwargs,
        operator=DagsterDockerOperator,
    )


def make_airflow_dag_containerized_for_handle(
    handle,
    pipeline_name,
    image,
    environment_dict=None,
    mode=None,
    dag_id=None,
    dag_description=None,
    dag_kwargs=None,
    op_kwargs=None,
):
    op_kwargs = check.opt_dict_param(op_kwargs, 'op_kwargs', key_type=check.string_types)
    op_kwargs['image'] = image

    return _make_airflow_dag(
        handle=handle,
        pipeline_name=pipeline_name,
        environment_dict=environment_dict,
        mode=mode,
        dag_id=dag_id,
        dag_description=dag_description,
        dag_kwargs=dag_kwargs,
        op_kwargs=op_kwargs,
        operator=DagsterDockerOperator,
    )


def make_airflow_dag_kubernetized_for_handle(
    handle,
    pipeline_name,
    image,
    namespace,
    environment_dict=None,
    mode=None,
    dag_id=None,
    dag_description=None,
    dag_kwargs=None,
    op_kwargs=None,
):
    from .operators.kubernetes_operator import DagsterKubernetesPodOperator

    # See: https://github.com/dagster-io/dagster/issues/1663
    op_kwargs = check.opt_dict_param(op_kwargs, 'op_kwargs', key_type=check.string_types)
    op_kwargs['image'] = image
    op_kwargs['namespace'] = namespace

    return _make_airflow_dag(
        handle=handle,
        pipeline_name=pipeline_name,
        environment_dict=environment_dict,
        mode=mode,
        dag_id=dag_id,
        dag_description=dag_description,
        dag_kwargs=dag_kwargs,
        op_kwargs=op_kwargs,
        operator=DagsterKubernetesPodOperator,
    )
