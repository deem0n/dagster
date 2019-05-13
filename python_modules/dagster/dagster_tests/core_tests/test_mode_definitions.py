import pytest

from dagster import (
    DagsterInvalidDefinitionError,
    DagsterInvariantViolationError,
    ModeDefinition,
    PipelineContextDefinition,
    PipelineDefinition,
    RunConfig,
    execute_pipeline,
    resource,
    solid,
    execute_solids,
)

from dagster.core.definitions.environment_schema import create_environment_type

from ..test_repository import (
    define_modeless_pipeline,
    define_multi_mode_pipeline,
    define_multi_mode_with_resources_pipeline,
    define_single_mode_pipeline,
)


def test_basic_mode_definition():
    pipeline_def = PipelineDefinition(
        name='takes a mode', solids=[], mode_definitions=[ModeDefinition()]
    )
    assert pipeline_def


def test_mode_takes_a_name():
    pipeline_def = PipelineDefinition(
        name='takes a mode', solids=[], mode_definitions=[ModeDefinition(name='a_mode')]
    )
    assert pipeline_def


def test_execute_modeless():
    pipeline_result = execute_pipeline(define_modeless_pipeline())
    assert pipeline_result.result_for_solid('return_one').transformed_value() == 1


def test_modeless_env_type_name():
    env_type = create_environment_type(define_modeless_pipeline())
    assert env_type.key == 'Modeless.Environment'
    assert env_type.name == 'Modeless.Environment'


def test_execute_single_mode():
    single_mode_pipeline = define_single_mode_pipeline()
    assert single_mode_pipeline.is_modeless is False
    assert single_mode_pipeline.is_single_mode is True

    assert (
        execute_pipeline(single_mode_pipeline).result_for_solid('return_two').transformed_value()
        == 2
    )

    assert (
        execute_pipeline(single_mode_pipeline, run_config=RunConfig(mode='the_mode'))
        .result_for_solid('return_two')
        .transformed_value()
        == 2
    )


def test_wrong_single_mode():
    with pytest.raises(DagsterInvariantViolationError):
        assert (
            execute_pipeline(define_single_mode_pipeline(), run_config=RunConfig(mode='wrong_mode'))
            .result_for_solid('return_two')
            .transformed_value()
            == 2
        )


def test_mode_and_context():
    with pytest.raises(DagsterInvalidDefinitionError):
        PipelineDefinition(
            name='both_context_and_resources',
            solids=[],
            context_definitions={'some_context': PipelineContextDefinition()},
            mode_definitions=[ModeDefinition()],
        )


def test_mode_double_default_name():
    with pytest.raises(DagsterInvalidDefinitionError) as ide:
        PipelineDefinition(
            name='double_default', solids=[], mode_definitions=[ModeDefinition(), ModeDefinition()]
        )

    assert (
        str(ide.value) == 'Two modes seen with the name "default" in "double_default". '
        'Modes must have unique names.'
    )


def test_mode_double_given_name():
    with pytest.raises(DagsterInvalidDefinitionError) as ide:
        PipelineDefinition(
            name='double_given',
            solids=[],
            mode_definitions=[ModeDefinition(name='given'), ModeDefinition(name='given')],
        )

    assert (
        str(ide.value) == 'Two modes seen with the name "given" in "double_given". '
        'Modes must have unique names.'
    )


def test_execute_multi_mode():
    multi_mode_pipeline = define_multi_mode_pipeline()

    assert (
        execute_pipeline(multi_mode_pipeline, run_config=RunConfig(mode='mode_one'))
        .result_for_solid('return_three')
        .transformed_value()
        == 3
    )

    assert (
        execute_pipeline(multi_mode_pipeline, run_config=RunConfig(mode='mode_two'))
        .result_for_solid('return_three')
        .transformed_value()
        == 3
    )


def test_execute_multi_mode_errors():
    multi_mode_pipeline = define_multi_mode_pipeline()

    with pytest.raises(DagsterInvariantViolationError):
        execute_pipeline(multi_mode_pipeline)

    with pytest.raises(DagsterInvariantViolationError):
        execute_pipeline(multi_mode_pipeline, run_config=RunConfig(mode='wrong_mode'))


def test_execute_multi_mode_with_resources():
    pipeline_def = define_multi_mode_with_resources_pipeline()

    add_mode_result = execute_pipeline(
        pipeline_def,
        run_config=RunConfig(mode='add_mode'),
        environment_dict={'resources': {'op': {'config': 2}}},
    )

    assert add_mode_result.result_for_solid('apply_to_three').transformed_value() == 5

    mult_mode_result = execute_pipeline(
        pipeline_def,
        run_config=RunConfig(mode='mult_mode'),
        environment_dict={'resources': {'op': {'config': 3}}},
    )

    assert mult_mode_result.result_for_solid('apply_to_three').transformed_value() == 9


def test_correct_env_type_names_for_named():
    pipeline_def = define_multi_mode_with_resources_pipeline()

    mult_type_name = create_environment_type(pipeline_def, 'mult_mode')
    assert mult_type_name.key == 'MultiModeWithResources.Mode.MultMode.Environment'
    assert mult_type_name.name == 'MultiModeWithResources.Mode.MultMode.Environment'

    assert (
        mult_type_name.fields['resources'].config_type.key
        == 'MultiModeWithResources.Mode.MultMode.Resources'
    )
    assert (
        mult_type_name.fields['resources'].config_type.name
        == 'MultiModeWithResources.Mode.MultMode.Resources'
    )

    add_type_name = create_environment_type(pipeline_def, 'add_mode')

    assert add_type_name.key == 'MultiModeWithResources.Mode.AddMode.Environment'
    assert add_type_name.name == 'MultiModeWithResources.Mode.AddMode.Environment'

    assert (
        add_type_name.fields['resources'].config_type.key
        == 'MultiModeWithResources.Mode.AddMode.Resources'
    )
    assert (
        add_type_name.fields['resources'].config_type.name
        == 'MultiModeWithResources.Mode.AddMode.Resources'
    )


def test_mode_with_resource_deps():

    called = {'count': 0}

    @resource
    def resource_a(_init_context):
        return 1

    @solid(resources={'a'})
    def requires_a(context):
        called['count'] += 1
        assert context.resources.a == 1

    pipeline_def_good_deps = PipelineDefinition(
        name='mode_with_good_deps',
        solids=[requires_a],
        mode_definitions=[ModeDefinition(resources={'a': resource_a})],
    )

    execute_pipeline(pipeline_def_good_deps)

    assert called['count'] == 1

    with pytest.raises(DagsterInvalidDefinitionError) as ide:
        PipelineDefinition(
            name='mode_with_bad_deps',
            solids=[requires_a],
            mode_definitions=[ModeDefinition(resources={'ab': resource_a})],
        )

    assert (
        str(ide.value)
        == 'Resource "a" is required by solid requires_a, but is not provided by mode "default"'
    )

    @solid
    def no_deps(context):
        called['count'] += 1
        assert context.resources.a == 1

    pipeline_def_no_deps = PipelineDefinition(
        name='mode_with_no_deps',
        solids=[no_deps],
        mode_definitions=[ModeDefinition(resources={'a': resource_a})],
    )

    execute_pipeline(pipeline_def_no_deps)

    assert called['count'] == 2


def test_subset_with_mode_definitions():

    called = {'a': 0, 'b': 0}

    @resource
    def resource_a(_init_context):
        return 1

    @solid(resources={'a'})
    def requires_a(context):
        called['a'] += 1
        assert context.resources.a == 1

    @resource
    def resource_b(_init_context):
        return 2

    @solid(resources={'b'})
    def requires_b(context):
        called['b'] += 1
        assert context.resources.b == 2

    pipeline_def = PipelineDefinition(
        name='subset_test',
        solids=[requires_a, requires_b],
        mode_definitions=[ModeDefinition(resources={'a': resource_a, 'b': resource_b})],
    )

    assert execute_pipeline(pipeline_def).success is True

    assert called == {'a': 1, 'b': 1}

    assert execute_solids(pipeline_def, solid_names=['requires_a'])['requires_a'].success is True

    assert called == {'a': 2, 'b': 1}