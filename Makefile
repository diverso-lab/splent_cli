# Bare `make` shows the target list instead of silently running setup.
.DEFAULT_GOAL := help

# Recursive targets (cli → enter, setup-rebuild → cli-enter) call $(MAKE).
# Suppress the noisy "Entering/Leaving directory '…'" lines those print.
MAKEFLAGS += --no-print-directory

include makefiles/Makefile.setup
include makefiles/Makefile.cli

.PHONY: help

help: ## Show this help (list of make targets)
	@echo ""
	@echo "SPLENT CLI — host-side Make targets"
	@echo ""
	@grep -hE '^[a-zA-Z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
