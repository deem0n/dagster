FROM dagster/dagster-py37:latest

WORKDIR /opt/dagster

ADD dagster dagster
ADD dagster-graphql dagster-graphql
ADD dagster-aws dagster-aws
ADD dagster-cron dagster-cron
ADD dagster-gcp dagster-gcp
ADD dagster-k8s dagster-k8s
ADD examples examples

RUN pip install \
    -e dagster \
    -e dagster-graphql \
    -e dagster-aws \
    -e dagster-cron \
    -e dagster-gcp \
    -e dagster-k8s \
    -e "examples[full]"

EXPOSE 3000

WORKDIR /opt/dagster/examples/
