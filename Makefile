.PHONY: help bootstrap config build build-web up down restart ps logs health acceptance shell clean worker-install worker-uninstall worker-status worker-logs

SHELL := /bin/bash
REPO := $(shell cd "$(dir $(lastword $(MAKEFILE_LIST)))" && pwd)
STUDIO_HOME ?= $(HOME)/CreatorStudio
CFG := $(STUDIO_HOME)/config
COMPOSE := docker compose -p creatorstudio --env-file $(CFG)/compose.env -f $(CFG)/compose.yml
PY := $(shell sh $(REPO)/deploy/py3.sh 2>/dev/null || echo python3)

help:
	@echo "Creator Studio — local stack"
	@echo
	@echo "  make bootstrap   one-time: render config, generate credentials, seed dirs"
	@echo "  make build       build backend images (server, ai)"
	@echo "  make build-web   build the Next.js web image"
	@echo "  make up          start everything (loopback only)"
	@echo "  make down        stop everything (keeps data)"
	@echo "  make ps          container status"
	@echo "  make logs        tail logs (S=service to narrow)"
	@echo "  make health      probe :8080 + :9001"
	@echo "  make acceptance  full acceptance run (needs worker token for render)"
	@echo "  make worker-install  install the worker as a LaunchAgent (run in Terminal)"
	@echo "  make worker-status   is the LaunchAgent loaded, and its pid"
	@echo "  make clean       stop and DROP all data volumes"

bootstrap:
	@$(PY) deploy/render_config.py
	@echo "config written to $(CFG)"

config:
	@$(PY) deploy/render_config.py

build:
	@./deploy/build_images.sh

build-web:
	@./deploy/build_web.sh

up:
	@$(COMPOSE) up -d
	@echo "waiting for health..."
	@for i in $$(seq 1 40); do \
	  bad=$$($(COMPOSE) ps --format '{{.Service}} {{.State}} {{.Health}}' | awk '$$2!="running" || ($$3!="" && $$3!="healthy")'); \
	  [ -z "$$bad" ] && break; sleep 3; \
	done
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}'
	@echo
	@echo "workbench: http://127.0.0.1:8080"

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) restart

ps:
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}'

logs:
	@$(COMPOSE) logs -f --tail 100 $(S)

health:
	@printf "web   "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 http://127.0.0.1:8080/
	@printf "api   "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 http://127.0.0.1:8080/api/
	@printf "s3    "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 http://127.0.0.1:9001/

acceptance:
	@$(PY) deploy/acceptance.py

worker-install:
	@./deploy/worker_agent.sh install

worker-uninstall:
	@./deploy/worker_agent.sh uninstall

worker-status:
	@./deploy/worker_agent.sh status

worker-logs:
	@tail -f $(STUDIO_HOME)/logs/worker-stdout.log

clean:
	@echo "this deletes $(STUDIO_HOME)/data and all volumes"
	@read -p "type 'yes' to continue: " ok; [ "$$ok" = "yes" ] && $(COMPOSE) down -v || echo aborted
