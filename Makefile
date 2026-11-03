.PHONY: help bootstrap config build build-web up down restart ps logs health s3-bucket s3-check acceptance shell clean worker-install worker-uninstall worker-status worker-logs

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
	@echo "  make health      probe web, api and the S3 gateway"
	@echo "  make s3-bucket   ensure the object-storage bucket exists and is publicly readable"
	@echo "  make s3-check    upload a probe object and read it back three ways (direct / /oss/ / presigned)"
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
	@$(MAKE) --no-print-directory s3-bucket
	@echo
	@echo "workbench: http://127.0.0.1:8080  (en / zh-CN)"
	@echo "s3:        http://127.0.0.1:9000  (via nginx)"

down:
	@$(COMPOSE) down

restart:
	@$(COMPOSE) restart

ps:
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}'

logs:
	@$(COMPOSE) logs -f --tail 100 $(S)

health:
	@printf "web     "; curl -sL -o /dev/null -w "%{http_code}\n" --max-time 10 http://127.0.0.1:8080/
	@printf "api     "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 http://127.0.0.1:8080/api/health
	# 网关根路径匿名访问一律 403（没有匿名 list 权限），403 就说明 S3 栈活着。
	@printf "s3      "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 http://127.0.0.1:9000/

s3-bucket:
	# 上游用 mc 做这件事；这里用纯标准库 SigV4，幂等，缺 bucket 会建、缺匿名读策略会补。
	@$(PY) deploy/ensure_bucket.py --endpoint http://127.0.0.1:9000

s3-check:
	# 三条读回路径都要通：S3 直连、nginx /oss/<key>（服务端读）、presigned URL（客户端读）。
	# 探针对象用完即删，不在桶里留垃圾。
	@$(PY) deploy/check_s3_roundtrip.py

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
