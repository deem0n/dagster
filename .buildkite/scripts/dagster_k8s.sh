#! /bin/bash
ROOT=$(git rev-parse --show-toplevel)

set -eu

TOX_PYTHON_VERSION="$1"

# Environment vars
export DAGSTER_AIRFLOW_DOCKER_IMAGE="${AWS_ACCOUNT_ID}.dkr.ecr.us-west-1.amazonaws.com/dagster-k8s-demo:${BUILDKITE_BUILD_ID}"
export CLUSTER_NAME=kind`echo ${BUILDKITE_JOB_ID} | sed -e 's/-//g'`
export KUBECONFIG="/tmp/kubeconfig"

# ensure cleanup happens on error or normal exit
function cleanup {
    kind delete cluster --name ${CLUSTER_NAME}
}
trap cleanup EXIT

# Need a unique cluster name for this job; can't have hyphens
kind create cluster --name ${CLUSTER_NAME}
kind get kubeconfig --internal --name ${CLUSTER_NAME} > ${KUBECONFIG}

# see https://kind.sigs.k8s.io/docs/user/private-registries/#use-an-access-token
aws ecr get-login --no-include-email --region us-west-1 | sh
for node in $(kubectl get nodes -oname); do
    # the -oname format is kind/name (so node/name) we just want name
    node_name=${node#node/}
    # copy the config to where kubelet will look
    docker cp $HOME/.docker/config.json ${node_name}:/var/lib/kubelet/config.json
    # restart kubelet to pick up the config
    docker exec ${node_name} systemctl restart kubelet.service
done

cd  $ROOT/python_modules/libraries/dagster-k8s/

# Install Helm 3
curl https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 | bash

# Install helm chart
helm install \
    --set dagit.image.repository="${AWS_ACCOUNT_ID}.dkr.ecr.us-west-1.amazonaws.com/dagster-k8s-demo" \
    --set dagit.image.tag="${BUILDKITE_BUILD_ID}" \
    --set job_image.image.repository="${AWS_ACCOUNT_ID}.dkr.ecr.us-west-1.amazonaws.com/dagster-k8s-demo" \
    --set job_image.image.tag="${BUILDKITE_BUILD_ID}" \
    dagster \
    helm/dagster/

# Finally, run tests
tox -e $TOX_PYTHON_VERSION