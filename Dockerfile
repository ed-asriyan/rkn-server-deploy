FROM alpine/ansible
RUN apk add --no-cache curl make git
COPY id_rsa /root/.ssh/id_rsa
COPY known_hosts /root/.ssh/known_hosts
