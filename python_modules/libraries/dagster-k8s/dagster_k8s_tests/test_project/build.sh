#! /bin/bash
ROOT=$(git rev-parse --show-toplevel)
pushd $ROOT/python_modules/libraries/dagster-k8s/dagster_k8s_tests/test_project

set -ux

function cleanup {
    rm -rf dagster
    rm -rf dagster-graphql
    rm -rf dagster-aws
    rm -rf dagster-cron
    rm -rf dagster-gcp
    rm -rf dagster-k8s
    rm -rf examples
    set +ux
    popd
}
# # ensure cleanup happens on error or normal exit
trap cleanup INT TERM EXIT ERR

cp -R ../../../../dagster . && \
cp -R ../../../../dagster-graphql . && \
cp -R ../../../dagster-aws . && \
cp -R ../../../dagster-cron . && \
cp -R ../../../dagster-gcp . && \
cp -R ../../../../../examples . && \
rsync -av --progress ../../../dagster-k8s . --exclude dagster_k8s_tests
\
find . -name '*.egg-info' | xargs rm -rf && \
find . -name '*.tox' | xargs rm -rf && \
find . -name 'build' | xargs rm -rf && \
find . -name 'dist' | xargs rm -rf && \
\
docker build -t dagster-k8s-demo .
