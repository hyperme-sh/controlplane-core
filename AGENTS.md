# Core development

Runtime code lives under src/api. Preserve legacy alias module identity, settings,
session context, public routes and owner trust boundaries. Database migrations
remain owned by controlplane-db. Read README.md and run bash scripts/check-package.sh.
Keep immutable dependency pins in standard metadata and uv.lock. Consumer service
and live-provider validation remain separate from package checks.
