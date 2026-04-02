# faceguard Makefile
#
# Targets:
#   make setup           — scaffold ~/.faceguard/, copy config template
#   make install         — install guard LaunchAgent (runs at login)
#   make install-lmstudio — install optional LM Studio autostart agent
#   make uninstall       — unload and remove guard LaunchAgent
#   make uninstall-all   — unload and remove both LaunchAgents
#   make status          — show load status of all LaunchAgents
#   make test            — dry-run the guard right now (no siren, no Discord)
#   make enroll          — interactive: add your face to the roster
#   make verify          — test your face against the roster right now
#   make logs            — tail today's structured log
#   make logs-raw        — tail the LaunchAgent stdout/stderr
#   make clean-logs      — delete all log files older than 30 days
#   make check           — verify Python, deps, and config are all ready
#
# ── Configuration ──────────────────────────────────────────────────────────────
# All variables are auto-detected from the current environment.
# Override on the command line if needed: make install PYTHON=/custom/python3
#
# uv workflow (recommended):
#   uv venv .venv
#   uv pip install -r requirements.txt
#   uv pip install -r requirements-dev.txt
#   make setup
#
# The Makefile detects the .venv Python automatically — you do not need to
# activate the venv before running make targets.

# Absolute path to this Makefile's directory (the project root)
PROJECT_DIR := $(shell pwd)

# Python binary detection — prefers .venv created by uv or pip, falls back to system python3.
# The LaunchAgent plist uses this absolute path directly (no shell activation at login time).
VENV_PYTHON   := $(PROJECT_DIR)/.venv/bin/python3
SYSTEM_PYTHON := $(shell which python3 2>/dev/null)
PYTHON        := $(shell [ -f "$(VENV_PYTHON)" ] && echo "$(VENV_PYTHON)" || echo "$(SYSTEM_PYTHON)")

# Current user's home directory (LaunchAgents must go here)
HOME_DIR     := $(HOME)

# LaunchAgents destination
AGENTS_DIR   := $(HOME_DIR)/Library/LaunchAgents

# Agent labels and plist paths
GUARD_LABEL   := com.faceguard.guard
GUARD_PLIST   := $(AGENTS_DIR)/$(GUARD_LABEL).plist
LMS_LABEL     := com.faceguard.lmstudio
LMS_PLIST     := $(AGENTS_DIR)/$(LMS_LABEL).plist

# lms CLI location (LM Studio installs this)
LMS_CLI       := $(shell which lms 2>/dev/null || echo "/usr/local/bin/lms")

# Config and data dir
FACEGUARD_DIR := $(HOME_DIR)/.faceguard
CONFIG_FILE   := $(FACEGUARD_DIR)/config.json

# Colours for output (works in any terminal)
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
RESET  := \033[0m
BOLD   := \033[1m

# ── Phony targets ──────────────────────────────────────────────────────────────
.PHONY: setup install install-lmstudio uninstall uninstall-all \
        status test test-suite enroll verify logs logs-raw clean-logs check diagnose \
        _require-python _require-config _require-enrolled _check-plist-installed

# ── Default target ─────────────────────────────────────────────────────────────
.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "$(BOLD)faceguard$(RESET) — login face recognition guard"
	@echo ""
	@echo "$(BOLD)First time setup:$(RESET)"
	@echo "  make setup          Scaffold ~/.faceguard/ and create config"
	@echo "  make enroll         Add your face to the roster"
	@echo "  make verify         Test your face against the roster"
	@echo "  make test           Dry-run the guard (no siren, no Discord)"
	@echo "  make install        Install the guard to run at every login"
	@echo ""
	@echo "$(BOLD)Management:$(RESET)"
	@echo "  make status         Show LaunchAgent load status"
	@echo "  make logs           Tail today's structured log"
	@echo "  make uninstall      Remove guard from login items"
	@echo ""
	@echo "$(BOLD)Optional:$(RESET)"
	@echo "  make install-lmstudio  Auto-start LM Studio server at login"
	@echo "  make check             Verify environment is ready"
	@echo "  make diagnose          Debug setup issues without prereqs"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────────────────
setup: _require-python
	@echo "$(BOLD)Setting up faceguard...$(RESET)"
	@$(PYTHON) setup.py
	@echo ""
	@echo "$(GREEN)Done.$(RESET) Edit $(CONFIG_FILE) then run: make enroll"

# ── Install guard LaunchAgent ──────────────────────────────────────────────────
install: _require-python _require-config _require-enrolled
	@echo "$(BOLD)Installing faceguard LaunchAgent...$(RESET)"

	@# Unload existing agent if already installed (clean reinstall)
	@if launchctl list | grep -q "$(GUARD_LABEL)" 2>/dev/null; then \
		echo "  Unloading existing agent..."; \
		launchctl unload "$(GUARD_PLIST)" 2>/dev/null || true; \
	fi

	@# Substitute placeholders in template → installed plist
	@mkdir -p "$(AGENTS_DIR)"
	@sed \
		-e 's|__PYTHON__|$(PYTHON)|g' \
		-e 's|__PROJECT_DIR__|$(PROJECT_DIR)|g' \
		-e 's|__HOME__|$(HOME_DIR)|g' \
		scripts/com.faceguard.guard.plist.template \
		> "$(GUARD_PLIST)"

	@echo "  Plist written to: $(GUARD_PLIST)"

	@# Verify the substitution worked — Python path must exist
	@if [ ! -f "$(PYTHON)" ]; then \
		echo "$(RED)Error: Python binary not found at $(PYTHON)$(RESET)"; \
		echo "  Create a venv first:  uv venv .venv"; \
		echo "  Install deps:         uv pip install -r requirements.txt"; \
		rm -f "$(GUARD_PLIST)"; \
		exit 1; \
	fi

	@# Load the agent
	@launchctl load "$(GUARD_PLIST)"
	@echo "  LaunchAgent loaded: $(GUARD_LABEL)"
	@echo ""
	@echo "$(GREEN)✓ Guard installed.$(RESET) It will run at next login."
	@echo "  To test now:  make test"
	@echo "  To check status: make status"

# ── Install LM Studio LaunchAgent (optional) ──────────────────────────────────
install-lmstudio: _require-python
	@echo "$(BOLD)Installing LM Studio autostart...$(RESET)"
	@LMS_PATH=$$(which lms 2>/dev/null || echo "$(LMS_CLI)"); \
	MODEL=$$($(PYTHON) -c 'import json; print(json.load(open("$(CONFIG_FILE)"))["lm_studio"]["model"])'); \
	sed \
		-e "s|__LMS__|$$LMS_PATH|g" \
		-e "s|__HOME__|$(HOME_DIR)|g" \
		-e "s|__PROJECT_DIR__|$(PROJECT_DIR)|g" \
		-e "s|__MODEL__|$$MODEL|g" \
		scripts/com.faceguard.lmstudio.plist.template \
		> "$(LMS_PLIST)"
	@launchctl unload "$(LMS_PLIST)" 2>/dev/null || true
	@launchctl load "$(LMS_PLIST)"
	@echo "$(GREEN)✓ LM Studio autostart installed.$(RESET)"

	
# ── Uninstall ──────────────────────────────────────────────────────────────────
uninstall:
	@echo "$(BOLD)Uninstalling faceguard LaunchAgent...$(RESET)"
	@if launchctl list | grep -q "$(GUARD_LABEL)" 2>/dev/null; then \
		launchctl unload "$(GUARD_PLIST)" && echo "  Unloaded: $(GUARD_LABEL)"; \
	else \
		echo "  $(GUARD_LABEL) was not loaded."; \
	fi
	@if [ -f "$(GUARD_PLIST)" ]; then \
		rm "$(GUARD_PLIST)" && echo "  Removed: $(GUARD_PLIST)"; \
	else \
		echo "  Plist not found — nothing to remove."; \
	fi
	@echo "$(GREEN)Done.$(RESET) Guard will no longer run at login."
	@echo "  Your roster and config in ~/.faceguard/ are untouched."

uninstall-all: uninstall
	@echo "$(BOLD)Uninstalling LM Studio autostart...$(RESET)"
	@if launchctl list | grep -q "$(LMS_LABEL)" 2>/dev/null; then \
		launchctl unload "$(LMS_PLIST)" && echo "  Unloaded: $(LMS_LABEL)"; \
	else \
		echo "  $(LMS_LABEL) was not loaded."; \
	fi
	@if [ -f "$(LMS_PLIST)" ]; then \
		rm "$(LMS_PLIST)" && echo "  Removed: $(LMS_PLIST)"; \
	fi
	@echo "$(GREEN)Done.$(RESET)"

# ── Status ─────────────────────────────────────────────────────────────────────
status:
	@echo "$(BOLD)LaunchAgent status:$(RESET)"
	@echo ""

	@# Guard agent
	@if launchctl list | grep -q "$(GUARD_LABEL)" 2>/dev/null; then \
		STATUS=$$(launchctl list | grep "$(GUARD_LABEL)"); \
		PID=$$(echo "$$STATUS" | awk '{print $$1}'); \
		EXITCODE=$$(echo "$$STATUS" | awk '{print $$2}'); \
		echo "  Guard:    $(GREEN)loaded$(RESET)  (PID: $$PID, last exit: $$EXITCODE)"; \
	else \
		echo "  Guard:    $(RED)not loaded$(RESET)  — run: make install"; \
	fi

	@# LM Studio agent
	@if launchctl list | grep -q "$(LMS_LABEL)" 2>/dev/null; then \
		STATUS=$$(launchctl list | grep "$(LMS_LABEL)"); \
		PID=$$(echo "$$STATUS" | awk '{print $$1}'); \
		EXITCODE=$$(echo "$$STATUS" | awk '{print $$2}'); \
		echo "  LMStudio: $(GREEN)loaded$(RESET)  (PID: $$PID, last exit: $$EXITCODE)"; \
	else \
		echo "  LMStudio: $(YELLOW)not loaded$(RESET)  (optional — run: make install-lmstudio)"; \
	fi

	@echo ""

	@# Python binary used
	@echo "  Python:   $(PYTHON)"
	@if [ -f "$(PYTHON)" ]; then \
		VERSION=$$($(PYTHON) --version 2>&1); \
		echo "            $$VERSION"; \
	else \
		echo "            $(RED)not found$(RESET)"; \
	fi

	@# Config status
	@if [ -f "$(CONFIG_FILE)" ]; then \
		echo "  Config:   $(GREEN)present$(RESET)  ($(CONFIG_FILE))"; \
	else \
		echo "  Config:   $(RED)missing$(RESET)  — run: make setup"; \
	fi

	@# Roster status
	@if [ -f "$(FACEGUARD_DIR)/roster.pkl" ]; then \
		echo "  Roster:   $(GREEN)present$(RESET)"; \
	else \
		echo "  Roster:   $(RED)empty$(RESET)  — run: make enroll"; \
	fi
	@echo ""

# ── Test (dry run) ─────────────────────────────────────────────────────────────
test: _require-python _require-config _require-enrolled
	@echo "$(BOLD)Running dry-run test...$(RESET)"
	@echo "  Recognition will run. No siren, no Discord."
	@echo ""
	@$(PYTHON) face_guard.py --dry-run --no-delay --verbose
	@echo ""
	@echo "$(GREEN)Dry run complete.$(RESET)"

test-suite: _require-python
	@echo "$(BOLD)Running test suite...$(RESET)"
	@$(PYTHON) -m pytest tests/ -v --tb=short
	@echo ""

# ── Enrollment ─────────────────────────────────────────────────────────────────
enroll: _require-python _require-config
	@echo "$(BOLD)Face enrollment$(RESET)"
	@echo ""
	@read -p "  Enter your name: " NAME; \
	$(PYTHON) enroll.py add "$$NAME"

verify: _require-python _require-config
	@$(PYTHON) enroll.py verify

# ── Logs ───────────────────────────────────────────────────────────────────────
logs: 
	@LOG_FILE="$(FACEGUARD_DIR)/logs/$$(date +%Y-%m-%d).jsonl"; \
	if [ -f "$$LOG_FILE" ]; then \
		echo "$(BOLD)Tailing: $$LOG_FILE$(RESET)"; \
		echo ""; \
		tail -f "$$LOG_FILE" | $(PYTHON) scripts/pretty_logs.py; \
	else \
		echo "$(YELLOW)No log file for today.$(RESET)"; \
		echo "  Run the guard first: make test"; \
	fi

logs-raw:
	@echo "$(BOLD)LaunchAgent stdout:$(RESET)"
	@cat "$(FACEGUARD_DIR)/logs/launchagent.out" 2>/dev/null || echo "  (empty)"
	@echo ""
	@echo "$(BOLD)LaunchAgent stderr:$(RESET)"
	@cat "$(FACEGUARD_DIR)/logs/launchagent.err" 2>/dev/null || echo "  (empty)"

clean-logs:
	@echo "Removing log files older than 30 days..."
	@find "$(FACEGUARD_DIR)/logs" -name "*.jsonl" -mtime +30 -delete -print 2>/dev/null || true
	@echo "$(GREEN)Done.$(RESET)"

# ── Diagnose (no prereqs — runs even when things are broken) ──────────────────
diagnose:
	@echo "$(BOLD)faceguard diagnose$(RESET)"
	@echo ""

	@# Python detection
	@echo "  Detected Python: $(PYTHON)"
	@if [ -f "$(PYTHON)" ]; then \
		VERSION=$$($(PYTHON) --version 2>&1); \
		echo "  Python version:  $$VERSION  $(GREEN)✓$(RESET)"; \
	else \
		echo "  Python:          $(RED)not found$(RESET)"; \
		echo "  → Run:  uv venv .venv && uv pip install -r requirements.txt"; \
	fi

	@echo ""

	@# Config file
	@if [ ! -f "$(CONFIG_FILE)" ]; then \
		echo "  Config:  $(RED)missing$(RESET) — run: make setup"; \
	else \
		echo "  Config:  $(GREEN)present$(RESET) ($(CONFIG_FILE))"; \
		$(PYTHON) -c " \
import json, sys; \
try: \
    cfg = json.load(open('$(CONFIG_FILE)')); \
    url = cfg.get('discord',{}).get('webhook_url',''); \
    if not url or 'YOUR_WEBHOOK' in url: \
        print('  Webhook: \033[0;31mnot set\033[0m  — edit $(CONFIG_FILE) and replace YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN'); \
    else: \
        print('  Webhook: \033[0;32mset\033[0m  (' + url[:40] + '...)'); \
except json.JSONDecodeError as e: \
    print('  Config:  \033[0;31minvalid JSON\033[0m  —  ' + str(e)); \
    print('  → Open $(CONFIG_FILE) and fix the JSON (check for trailing commas, missing quotes)'); \
" 2>&1; \
	fi

	@echo ""

	@# face_recognition
	@if $(PYTHON) -c "import face_recognition" 2>/dev/null; then \
		echo "  face_recognition: $(GREEN)installed$(RESET)"; \
	else \
		echo "  face_recognition: $(RED)missing$(RESET)"; \
		echo "  → brew install cmake && uv pip install face_recognition"; \
		echo "  → uv pip install git+https://github.com/ageitgey/face_recognition_models"; \
	fi

	@# face_recognition_models (separate package — common missing dep)
	@if $(PYTHON) -c "import face_recognition_models" 2>/dev/null; then \
		echo "  face_recognition_models: $(GREEN)installed$(RESET)"; \
	else \
		echo "  face_recognition_models: $(RED)missing$(RESET)"; \
		echo "  → uv pip install git+https://github.com/ageitgey/face_recognition_models"; \
	fi

	@# opencv
	@if $(PYTHON) -c "import cv2" 2>/dev/null; then \
		VERSION=$$($(PYTHON) -c "import cv2; print(cv2.__version__)"); \
		echo "  opencv:          $(GREEN)installed$(RESET)  ($$VERSION)"; \
	else \
		echo "  opencv:          $(RED)missing$(RESET)  — uv pip install opencv-python"; \
	fi

	@echo ""

	@# Roster
	@if [ -f "$(FACEGUARD_DIR)/roster.pkl" ]; then \
		echo "  Roster:  $(GREEN)present$(RESET)"; \
	else \
		echo "  Roster:  $(RED)empty$(RESET)  — run: make enroll"; \
	fi

	@echo ""

# ── Environment check ──────────────────────────────────────────────────────────
check: _require-python
	@echo "$(BOLD)Environment check$(RESET)"
	@echo ""

	@# Python version
	@VERSION=$$($(PYTHON) --version 2>&1); \
	echo "  Python:       $$VERSION  ($(PYTHON))"

	@# face_recognition
	@if $(PYTHON) -c "import face_recognition" 2>/dev/null; then \
		echo "  face_recognition: $(GREEN)installed$(RESET)"; \
	else \
		echo "  face_recognition: $(RED)missing$(RESET)  — brew install cmake && pip install face_recognition"; \
	fi

	@# opencv
	@if $(PYTHON) -c "import cv2" 2>/dev/null; then \
		VERSION=$$($(PYTHON) -c "import cv2; print(cv2.__version__)"); \
		echo "  opencv-python:    $(GREEN)installed$(RESET)  ($$VERSION)"; \
	else \
		echo "  opencv-python:    $(RED)missing$(RESET)  — pip install opencv-python"; \
	fi

	@# requests
	@if $(PYTHON) -c "import requests" 2>/dev/null; then \
		echo "  requests:         $(GREEN)installed$(RESET)"; \
	else \
		echo "  requests:         $(RED)missing$(RESET)  — pip install requests"; \
	fi

	@# config
	@if [ -f "$(CONFIG_FILE)" ]; then \
		echo "  config.json:      $(GREEN)present$(RESET)"; \
		if $(PYTHON) -c " \
import json, sys; \
cfg = json.load(open('$(CONFIG_FILE)')); \
url = cfg.get('discord',{}).get('webhook_url',''); \
sys.exit(0 if url and 'YOUR_WEBHOOK' not in url else 1) \
" 2>/dev/null; then \
			echo "  discord webhook:  $(GREEN)configured$(RESET)"; \
		else \
			echo "  discord webhook:  $(RED)not set$(RESET)  — edit $(CONFIG_FILE)"; \
		fi; \
	else \
		echo "  config.json:      $(RED)missing$(RESET)  — run: make setup"; \
	fi

	@# roster
	@if [ -f "$(FACEGUARD_DIR)/roster.pkl" ]; then \
		COUNT=$$($(PYTHON) -c " \
import sys, pickle; \
sys.path.insert(0, '.'); \
from faceguard.roster import Roster; \
from pathlib import Path; \
r = Roster.load(Path('$(FACEGUARD_DIR)/roster.pkl')); \
print(len(r)) \
" 2>/dev/null); \
		echo "  roster:           $(GREEN)present$(RESET)  ($$COUNT enrolled)"; \
	else \
		echo "  roster:           $(RED)empty$(RESET)  — run: make enroll"; \
	fi

	@# lms CLI
	@if which lms > /dev/null 2>&1; then \
		LMS_PATH=$$(which lms); \
		echo "  lms CLI:          $(GREEN)found$(RESET)  ($$LMS_PATH)"; \
	else \
		echo "  lms CLI:          $(YELLOW)not found$(RESET)  (optional — needed for make install-lmstudio)"; \
	fi

	@# uv (recommended package manager)
	@if which uv > /dev/null 2>&1; then \
		UV_VERSION=$$(uv --version 2>&1); \
		echo "  uv:               $(GREEN)found$(RESET)  ($$UV_VERSION)"; \
	else \
		echo "  uv:               $(YELLOW)not found$(RESET)  (optional — install: curl -LsSf https://astral.sh/uv/install.sh | sh)"; \
	fi

	@# afplay (siren)
	@if which afplay > /dev/null 2>&1; then \
		echo "  afplay:           $(GREEN)found$(RESET)  (siren will work)"; \
	else \
		echo "  afplay:           $(RED)not found$(RESET)  (not macOS? siren disabled)"; \
	fi

	@echo ""

# ── Internal prereq guards ─────────────────────────────────────────────────────
# These targets fail fast with a clear message rather than letting a downstream
# target fail with a cryptic Python traceback.

_require-python:
	@if [ -z "$(PYTHON)" ] || [ ! -f "$(PYTHON)" ]; then \
		echo "$(RED)Error: Python not found.$(RESET)"; \
		echo "  Create a venv:  uv venv .venv"; \
		echo "  Install deps:   uv pip install -r requirements.txt"; \
		exit 1; \
	fi

_require-config:
	@if [ ! -f "$(CONFIG_FILE)" ]; then \
		echo "$(RED)Error: Config not found at $(CONFIG_FILE).$(RESET)"; \
		echo "  Run: make setup"; \
		exit 1; \
	fi
	@$(PYTHON) scripts/check_config.py || exit 1

_require-enrolled:
	@if [ ! -f "$(FACEGUARD_DIR)/roster.pkl" ]; then \
		echo "$(RED)Error: No enrolled faces found.$(RESET)"; \
		echo "  Run: make enroll"; \
		exit 1; \
	fi