-- 休眠基线 DDL——禁止对现有测试数据库执行。
-- 现有 nodeskclaw_task.rpa_engine Schema 必须执行 Alembic stamp，
-- 不得通过此修订执行 upgrade。此 SQL 仅适用于全新数据库。

CREATE SCHEMA IF NOT EXISTS rpa_engine AUTHORIZATION task_user;

-- statement-break

CREATE TABLE rpa_engine.rpa_browser_profiles (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	profile_ref VARCHAR(128) NOT NULL,
	tenant_id VARCHAR(128) NOT NULL,
	portal_account_id VARCHAR(128) NOT NULL,
	owner_type VARCHAR(32) DEFAULT 'PORTAL_ACCOUNT'::character varying NOT NULL,
	storage_ref TEXT,
	allowed_worker_tags TEXT[] DEFAULT ARRAY[]::text[] NOT NULL,
	status VARCHAR(16) DEFAULT 'DISABLED'::character varying NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	created_by VARCHAR(128) NOT NULL,
	last_used_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_browser_profiles_pkey PRIMARY KEY (id),
	CONSTRAINT uq_rpa_browser_profiles_ref UNIQUE (profile_ref),
	CONSTRAINT ck_rpa_browser_profiles_active_storage CHECK (status <> 'ACTIVE' OR (storage_ref IS NOT NULL AND btrim(storage_ref) <> '')),
	CONSTRAINT ck_rpa_browser_profiles_metadata CHECK (jsonb_typeof(metadata) = 'object'),
	CONSTRAINT ck_rpa_browser_profiles_owner_type CHECK (owner_type IN ('PORTAL_ACCOUNT', 'TENANT')),
	CONSTRAINT ck_rpa_browser_profiles_portal CHECK (btrim(portal_account_id) <> ''),
	CONSTRAINT ck_rpa_browser_profiles_ref CHECK (btrim(profile_ref) <> ''),
	CONSTRAINT ck_rpa_browser_profiles_status CHECK (status IN ('DISABLED', 'ACTIVE', 'LOCKED', 'REVOKED')),
	CONSTRAINT ck_rpa_browser_profiles_tenant CHECK (btrim(tenant_id) <> '')
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_browser_profiles IS 'Future PERSISTENT_PROFILE metadata; disabled in P0';

-- statement-break

CREATE TABLE rpa_engine.rpa_cdp_endpoints (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	endpoint_ref VARCHAR(128) NOT NULL,
	tenant_id VARCHAR(128) NOT NULL,
	portal_account_id VARCHAR(128),
	endpoint_kind VARCHAR(16) DEFAULT 'REMOTE'::character varying NOT NULL,
	connection_secret_ref VARCHAR(255),
	allowed_worker_tags TEXT[] DEFAULT ARRAY[]::text[] NOT NULL,
	status VARCHAR(16) DEFAULT 'DISABLED'::character varying NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	created_by VARCHAR(128) NOT NULL,
	last_verified_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_cdp_endpoints_pkey PRIMARY KEY (id),
	CONSTRAINT uq_rpa_cdp_endpoints_ref UNIQUE (endpoint_ref),
	CONSTRAINT ck_rpa_cdp_endpoints_active_secret CHECK (status <> 'ACTIVE' OR (connection_secret_ref IS NOT NULL AND btrim(connection_secret_ref) <> '')),
	CONSTRAINT ck_rpa_cdp_endpoints_kind CHECK (endpoint_kind IN ('LOCAL', 'REMOTE', 'MANAGED')),
	CONSTRAINT ck_rpa_cdp_endpoints_metadata CHECK (jsonb_typeof(metadata) = 'object'),
	CONSTRAINT ck_rpa_cdp_endpoints_ref CHECK (btrim(endpoint_ref) <> ''),
	CONSTRAINT ck_rpa_cdp_endpoints_status CHECK (status IN ('DISABLED', 'ACTIVE', 'REVOKED')),
	CONSTRAINT ck_rpa_cdp_endpoints_tenant CHECK (btrim(tenant_id) <> '')
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_cdp_endpoints IS 'Future CDP_ATTACH references; disabled in P0';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_cdp_endpoints.connection_secret_ref IS 'Secret-manager reference only; never plaintext connection credentials';

-- statement-break

CREATE TABLE rpa_engine.rpa_flows (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	flow_key VARCHAR(255) NOT NULL,
	scope VARCHAR(16) NOT NULL,
	tenant_id VARCHAR(128),
	name VARCHAR(255) NOT NULL,
	description TEXT,
	status VARCHAR(16) DEFAULT 'ACTIVE'::character varying NOT NULL,
	labels TEXT[] DEFAULT ARRAY[]::text[] NOT NULL,
	created_by VARCHAR(128) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_flows_pkey PRIMARY KEY (id),
	CONSTRAINT ck_rpa_flows_flow_key_not_blank CHECK (btrim(flow_key) <> ''),
	CONSTRAINT ck_rpa_flows_name_not_blank CHECK (btrim(name) <> ''),
	CONSTRAINT ck_rpa_flows_scope CHECK (scope IN ('GLOBAL', 'TENANT')),
	CONSTRAINT ck_rpa_flows_scope_tenant CHECK ((scope = 'GLOBAL' AND tenant_id IS NULL) OR (scope = 'TENANT' AND tenant_id IS NOT NULL AND btrim(tenant_id) <> '')),
	CONSTRAINT ck_rpa_flows_status CHECK (status IN ('ACTIVE', 'DISABLED', 'ARCHIVED'))
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_flows IS 'Stable GLOBAL or TENANT RPA Flow identity';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_flows.flow_key IS 'Public rpaFlowId; stable across versions';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_flows.tenant_id IS 'External tenant reference; NULL only for GLOBAL Flow';

-- statement-break

CREATE TABLE rpa_engine.rpa_worker_instances (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	worker_id VARCHAR(64) NOT NULL,
	worker_type VARCHAR(32) NOT NULL,
	device_name VARCHAR(255) NOT NULL,
	status VARCHAR(16) DEFAULT 'OFFLINE'::character varying NOT NULL,
	capabilities TEXT[] NOT NULL,
	tags TEXT[] DEFAULT ARRAY[]::text[] NOT NULL,
	app_version VARCHAR(64),
	agent_version VARCHAR(64),
	os VARCHAR(128),
	max_concurrent_runs INTEGER DEFAULT 1 NOT NULL,
	current_task_count INTEGER DEFAULT 0 NOT NULL,
	browser_count INTEGER DEFAULT 0 NOT NULL,
	metadata JSONB DEFAULT '{}'::jsonb NOT NULL,
	registered_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	last_heartbeat_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_worker_instances_pkey PRIMARY KEY (id),
	CONSTRAINT uq_rpa_worker_instances_worker_id UNIQUE (worker_id),
	CONSTRAINT ck_rpa_worker_instances_capabilities CHECK (cardinality(capabilities) > 0),
	CONSTRAINT ck_rpa_worker_instances_concurrency CHECK (max_concurrent_runs > 0 AND current_task_count >= 0 AND current_task_count <= max_concurrent_runs AND browser_count >= 0),
	CONSTRAINT ck_rpa_worker_instances_metadata CHECK (jsonb_typeof(metadata) = 'object'),
	CONSTRAINT ck_rpa_worker_instances_status CHECK (status IN ('ONLINE', 'BUSY', 'OFFLINE', 'DRAINING')),
	CONSTRAINT ck_rpa_worker_instances_type CHECK (worker_type IN ('SERVER_WORKER', 'LOCAL_AGENT')),
	CONSTRAINT ck_rpa_worker_instances_worker_id CHECK (btrim(worker_id) <> '')
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_worker_instances IS 'Engine-internal Worker state; public.rpa_workers remains Task dispatch authority';

-- statement-break

CREATE TABLE rpa_engine.rpa_flow_versions (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	flow_id UUID NOT NULL,
	version VARCHAR(64) NOT NULL,
	status VARCHAR(16) DEFAULT 'DRAFT'::character varying NOT NULL,
	engine_type VARCHAR(32) DEFAULT 'PLAYWRIGHT_CDP'::character varying NOT NULL,
	entrypoint VARCHAR(255) DEFAULT 'flow.py:run'::character varying NOT NULL,
	manifest JSONB NOT NULL,
	supported_workflow_codes TEXT[] NOT NULL,
	supported_portal_types TEXT[] DEFAULT ARRAY[]::text[] NOT NULL,
	input_schema JSONB DEFAULT '[]'::jsonb NOT NULL,
	capabilities TEXT[] DEFAULT ARRAY[]::text[] NOT NULL,
	minimum_engine_version VARCHAR(64),
	package_bucket VARCHAR(255),
	package_object_key TEXT,
	package_size_bytes BIGINT,
	package_checksum_sha256 CHAR(64),
	created_by VARCHAR(128) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_flow_versions_pkey PRIMARY KEY (id),
	CONSTRAINT uq_rpa_flow_versions_flow_version UNIQUE (flow_id, version),
	CONSTRAINT ck_rpa_flow_versions_checksum CHECK (package_checksum_sha256 IS NULL OR package_checksum_sha256 ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_rpa_flow_versions_engine_type CHECK (engine_type = 'PLAYWRIGHT_CDP'),
	CONSTRAINT ck_rpa_flow_versions_entrypoint CHECK (entrypoint = 'flow.py:run'),
	CONSTRAINT ck_rpa_flow_versions_input_schema_array CHECK (jsonb_typeof(input_schema) = 'array'),
	CONSTRAINT ck_rpa_flow_versions_manifest_object CHECK (jsonb_typeof(manifest) = 'object'),
	CONSTRAINT ck_rpa_flow_versions_package_size CHECK (package_size_bytes IS NULL OR package_size_bytes >= 0),
	CONSTRAINT ck_rpa_flow_versions_published_package CHECK (status <> 'PUBLISHED' OR (published_at IS NOT NULL AND package_bucket IS NOT NULL AND btrim(package_bucket) <> '' AND package_object_key IS NOT NULL AND btrim(package_object_key) <> '' AND package_size_bytes IS NOT NULL AND package_checksum_sha256 IS NOT NULL)),
	CONSTRAINT ck_rpa_flow_versions_semver CHECK (version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'),
	CONSTRAINT ck_rpa_flow_versions_status CHECK (status IN ('DRAFT', 'VALIDATING', 'PUBLISHED', 'DEPRECATED', 'DISABLED')),
	CONSTRAINT ck_rpa_flow_versions_workflow_codes CHECK (cardinality(supported_workflow_codes) > 0),
	CONSTRAINT rpa_flow_versions_flow_id_fkey FOREIGN KEY(flow_id) REFERENCES rpa_engine.rpa_flows (id) ON DELETE RESTRICT
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_flow_versions IS 'Versioned immutable Flow manifest and package metadata';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_flow_versions.package_object_key IS 'Stable MinIO/S3 object key; never store a signed URL';

-- statement-break

CREATE TABLE rpa_engine.rpa_execution_attempts (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	dispatch_mode VARCHAR(16) NOT NULL,
	command_id VARCHAR(128),
	lease_id VARCHAR(128),
	task_id VARCHAR(128) NOT NULL,
	run_id VARCHAR(128) NOT NULL,
	workflow_binding_id VARCHAR(128),
	portal_account_id VARCHAR(128),
	worker_instance_id UUID,
	worker_id VARCHAR(64) NOT NULL,
	flow_version_id UUID NOT NULL,
	rpa_flow_id_snapshot VARCHAR(255) NOT NULL,
	rpa_flow_version_snapshot VARCHAR(64) NOT NULL,
	package_checksum_snapshot CHAR(64) NOT NULL,
	attempt_no INTEGER DEFAULT 1 NOT NULL,
	status VARCHAR(24) DEFAULT 'RECEIVED'::character varying NOT NULL,
	input_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL,
	browser_session_snapshot JSONB DEFAULT '{}'::jsonb NOT NULL,
	error_code VARCHAR(128),
	error_message TEXT,
	error_details JSONB DEFAULT '{}'::jsonb NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	ended_at TIMESTAMP WITH TIME ZONE,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_execution_attempts_pkey PRIMARY KEY (id),
	CONSTRAINT uq_rpa_execution_attempts_run_attempt UNIQUE (run_id, attempt_no),
	CONSTRAINT ck_rpa_execution_attempts_attempt_no CHECK (attempt_no > 0),
	CONSTRAINT ck_rpa_execution_attempts_browser_session CHECK (jsonb_typeof(browser_session_snapshot) = 'object'),
	CONSTRAINT ck_rpa_execution_attempts_checksum CHECK (package_checksum_snapshot ~ '^[0-9a-f]{64}$'),
	CONSTRAINT ck_rpa_execution_attempts_dispatch_mode CHECK (dispatch_mode IN ('LEASE', 'QUEUE')),
	CONSTRAINT ck_rpa_execution_attempts_dispatch_reference CHECK ((dispatch_mode = 'LEASE' AND lease_id IS NOT NULL AND btrim(lease_id) <> '') OR (dispatch_mode = 'QUEUE' AND command_id IS NOT NULL AND btrim(command_id) <> '')),
	CONSTRAINT ck_rpa_execution_attempts_error_details CHECK (jsonb_typeof(error_details) = 'object'),
	CONSTRAINT ck_rpa_execution_attempts_input CHECK (jsonb_typeof(input_snapshot) = 'object'),
	CONSTRAINT ck_rpa_execution_attempts_status CHECK (status IN ('RECEIVED', 'LEASED', 'RUNNING', 'SUCCESS', 'FAILED', 'WAITING_HUMAN', 'CANCELLED', 'ABANDONED')),
	CONSTRAINT ck_rpa_execution_attempts_terminal_time CHECK (((status IN ('SUCCESS', 'FAILED', 'WAITING_HUMAN', 'CANCELLED', 'ABANDONED')) AND ended_at IS NOT NULL) OR ((status NOT IN ('SUCCESS', 'FAILED', 'WAITING_HUMAN', 'CANCELLED', 'ABANDONED')) AND ended_at IS NULL)),
	CONSTRAINT ck_rpa_execution_attempts_time_order CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at),
	CONSTRAINT rpa_execution_attempts_worker_instance_id_fkey FOREIGN KEY(worker_instance_id) REFERENCES rpa_engine.rpa_worker_instances (id) ON DELETE SET NULL,
	CONSTRAINT rpa_execution_attempts_flow_version_id_fkey FOREIGN KEY(flow_version_id) REFERENCES rpa_engine.rpa_flow_versions (id) ON DELETE RESTRICT
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_execution_attempts IS 'Engine technical attempts; public.rpa_runs remains Task Run authority';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_execution_attempts.task_id IS 'External Task ID; no cross-Schema foreign key';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_execution_attempts.run_id IS 'External Run ID; no cross-Schema foreign key';

-- statement-break

CREATE TABLE rpa_engine.rpa_flow_release_audits (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	flow_id UUID NOT NULL,
	flow_version_id UUID,
	action VARCHAR(32) NOT NULL,
	from_status VARCHAR(16),
	to_status VARCHAR(16),
	actor_id VARCHAR(128) NOT NULL,
	reason TEXT,
	details JSONB DEFAULT '{}'::jsonb NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_flow_release_audits_pkey PRIMARY KEY (id),
	CONSTRAINT ck_rpa_flow_release_audit_action CHECK (action IN ('UPLOADED', 'VALIDATION_STARTED', 'VALIDATION_PASSED', 'VALIDATION_FAILED', 'PUBLISHED', 'DEPRECATED', 'DISABLED', 'ROLLED_BACK', 'STATUS_CHANGED')),
	CONSTRAINT ck_rpa_flow_release_audit_details CHECK (jsonb_typeof(details) = 'object'),
	CONSTRAINT rpa_flow_release_audits_flow_id_fkey FOREIGN KEY(flow_id) REFERENCES rpa_engine.rpa_flows (id) ON DELETE RESTRICT,
	CONSTRAINT rpa_flow_release_audits_flow_version_id_fkey FOREIGN KEY(flow_version_id) REFERENCES rpa_engine.rpa_flow_versions (id) ON DELETE RESTRICT
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_flow_release_audits IS 'Append-only Flow publication and status audit trail';

-- statement-break

CREATE TABLE rpa_engine.rpa_flow_validation_runs (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	flow_version_id UUID NOT NULL,
	trigger_type VARCHAR(16) NOT NULL,
	status VARCHAR(16) DEFAULT 'PENDING'::character varying NOT NULL,
	checks JSONB DEFAULT '[]'::jsonb NOT NULL,
	errors JSONB DEFAULT '[]'::jsonb NOT NULL,
	warnings JSONB DEFAULT '[]'::jsonb NOT NULL,
	result_summary TEXT,
	requested_by VARCHAR(128) NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE,
	ended_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_flow_validation_runs_pkey PRIMARY KEY (id),
	CONSTRAINT ck_rpa_flow_validation_checks_array CHECK (jsonb_typeof(checks) = 'array'),
	CONSTRAINT ck_rpa_flow_validation_errors_array CHECK (jsonb_typeof(errors) = 'array'),
	CONSTRAINT ck_rpa_flow_validation_status CHECK (status IN ('PENDING', 'RUNNING', 'PASSED', 'FAILED')),
	CONSTRAINT ck_rpa_flow_validation_terminal_time CHECK (status NOT IN ('PASSED', 'FAILED') OR ended_at IS NOT NULL),
	CONSTRAINT ck_rpa_flow_validation_time_order CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at),
	CONSTRAINT ck_rpa_flow_validation_trigger CHECK (trigger_type IN ('UPLOAD', 'MANUAL', 'PUBLISH', 'CI')),
	CONSTRAINT ck_rpa_flow_validation_warnings_array CHECK (jsonb_typeof(warnings) = 'array'),
	CONSTRAINT rpa_flow_validation_runs_flow_version_id_fkey FOREIGN KEY(flow_version_id) REFERENCES rpa_engine.rpa_flow_versions (id) ON DELETE RESTRICT
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_flow_validation_runs IS 'Upload, manual, publish, and CI Flow validation results';

-- statement-break

CREATE TABLE rpa_engine.rpa_callback_outbox (
	id UUID DEFAULT gen_random_uuid() NOT NULL,
	execution_attempt_id UUID NOT NULL,
	destination VARCHAR(32) DEFAULT 'NODESKCLAW_TASK'::character varying NOT NULL,
	callback_type VARCHAR(16) NOT NULL,
	aggregate_id VARCHAR(128) NOT NULL,
	sequence_no BIGINT NOT NULL,
	idempotency_key VARCHAR(255) NOT NULL,
	endpoint_path TEXT NOT NULL,
	payload JSONB NOT NULL,
	status VARCHAR(16) DEFAULT 'PENDING'::character varying NOT NULL,
	attempts INTEGER DEFAULT 0 NOT NULL,
	max_attempts INTEGER DEFAULT 10 NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	locked_by VARCHAR(128),
	locked_at TIMESTAMP WITH TIME ZONE,
	last_error TEXT,
	response_status INTEGER,
	sent_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	CONSTRAINT rpa_callback_outbox_pkey PRIMARY KEY (id),
	CONSTRAINT uq_rpa_callback_outbox_attempt_sequence UNIQUE (execution_attempt_id, sequence_no),
	CONSTRAINT uq_rpa_callback_outbox_idempotency UNIQUE (idempotency_key),
	CONSTRAINT ck_rpa_callback_outbox_attempts CHECK (attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts),
	CONSTRAINT ck_rpa_callback_outbox_destination CHECK (destination = 'NODESKCLAW_TASK'),
	CONSTRAINT ck_rpa_callback_outbox_endpoint CHECK (endpoint_path LIKE '/%%' AND endpoint_path NOT LIKE 'http://%%' AND endpoint_path NOT LIKE 'https://%%'),
	CONSTRAINT ck_rpa_callback_outbox_payload CHECK (jsonb_typeof(payload) = 'object'),
	CONSTRAINT ck_rpa_callback_outbox_response_status CHECK (response_status IS NULL OR (response_status >= 100 AND response_status <= 599)),
	CONSTRAINT ck_rpa_callback_outbox_sent CHECK (status <> 'SENT' OR sent_at IS NOT NULL),
	CONSTRAINT ck_rpa_callback_outbox_sequence CHECK (sequence_no > 0),
	CONSTRAINT ck_rpa_callback_outbox_status CHECK (status IN ('PENDING', 'IN_FLIGHT', 'RETRY', 'SENT', 'DEAD')),
	CONSTRAINT ck_rpa_callback_outbox_type CHECK (callback_type IN ('EVENT', 'ARTIFACT', 'FINISH')),
	CONSTRAINT rpa_callback_outbox_execution_attempt_id_fkey FOREIGN KEY(execution_attempt_id) REFERENCES rpa_engine.rpa_execution_attempts (id) ON DELETE RESTRICT
);

-- statement-break

COMMENT ON TABLE rpa_engine.rpa_callback_outbox IS 'Ordered, idempotent callbacks to nodeskclaw-task';

-- statement-break

COMMENT ON COLUMN rpa_engine.rpa_callback_outbox.endpoint_path IS 'Relative Task API path only; never store credentials or signed URLs';

-- statement-break

CREATE INDEX ix_rpa_browser_profiles_portal ON rpa_engine.rpa_browser_profiles (portal_account_id);

-- statement-break

CREATE INDEX ix_rpa_browser_profiles_tenant_status ON rpa_engine.rpa_browser_profiles (tenant_id, status);

-- statement-break

CREATE INDEX ix_rpa_browser_profiles_worker_tags_gin ON rpa_engine.rpa_browser_profiles USING gin (allowed_worker_tags);

-- statement-break

CREATE INDEX ix_rpa_cdp_endpoints_portal ON rpa_engine.rpa_cdp_endpoints (portal_account_id) WHERE portal_account_id IS NOT NULL;

-- statement-break

CREATE INDEX ix_rpa_cdp_endpoints_tenant_status ON rpa_engine.rpa_cdp_endpoints (tenant_id, status);

-- statement-break

CREATE INDEX ix_rpa_cdp_endpoints_worker_tags_gin ON rpa_engine.rpa_cdp_endpoints USING gin (allowed_worker_tags);

-- statement-break

CREATE INDEX ix_rpa_flows_labels_gin ON rpa_engine.rpa_flows USING gin (labels);

-- statement-break

CREATE INDEX ix_rpa_flows_status ON rpa_engine.rpa_flows (status);

-- statement-break

CREATE INDEX ix_rpa_flows_tenant_status ON rpa_engine.rpa_flows (tenant_id, status) WHERE scope = 'TENANT';

-- statement-break

CREATE UNIQUE INDEX uq_rpa_flows_global_flow_key ON rpa_engine.rpa_flows (flow_key) WHERE scope = 'GLOBAL';

-- statement-break

CREATE UNIQUE INDEX uq_rpa_flows_tenant_flow_key ON rpa_engine.rpa_flows (tenant_id, flow_key) WHERE scope = 'TENANT';

-- statement-break

CREATE INDEX ix_rpa_worker_instances_capabilities_gin ON rpa_engine.rpa_worker_instances USING gin (capabilities);

-- statement-break

CREATE INDEX ix_rpa_worker_instances_status_heartbeat ON rpa_engine.rpa_worker_instances (status, last_heartbeat_at);

-- statement-break

CREATE INDEX ix_rpa_worker_instances_tags_gin ON rpa_engine.rpa_worker_instances USING gin (tags);

-- statement-break

CREATE INDEX ix_rpa_flow_versions_capabilities_gin ON rpa_engine.rpa_flow_versions USING gin (capabilities);

-- statement-break

CREATE INDEX ix_rpa_flow_versions_flow_status ON rpa_engine.rpa_flow_versions (flow_id, status);

-- statement-break

CREATE INDEX ix_rpa_flow_versions_manifest_gin ON rpa_engine.rpa_flow_versions USING gin (manifest jsonb_path_ops);

-- statement-break

CREATE INDEX ix_rpa_flow_versions_portal_types_gin ON rpa_engine.rpa_flow_versions USING gin (supported_portal_types);

-- statement-break

CREATE INDEX ix_rpa_flow_versions_published_at ON rpa_engine.rpa_flow_versions (published_at DESC) WHERE published_at IS NOT NULL;

-- statement-break

CREATE INDEX ix_rpa_flow_versions_status ON rpa_engine.rpa_flow_versions (status);

-- statement-break

CREATE INDEX ix_rpa_flow_versions_workflow_codes_gin ON rpa_engine.rpa_flow_versions USING gin (supported_workflow_codes);

-- statement-break

CREATE INDEX ix_rpa_execution_attempts_retention ON rpa_engine.rpa_execution_attempts (ended_at) WHERE ended_at IS NOT NULL;

-- statement-break

CREATE INDEX ix_rpa_execution_attempts_run_received ON rpa_engine.rpa_execution_attempts (run_id, received_at DESC);

-- statement-break

CREATE INDEX ix_rpa_execution_attempts_status_received ON rpa_engine.rpa_execution_attempts (status, received_at);

-- statement-break

CREATE INDEX ix_rpa_execution_attempts_task_received ON rpa_engine.rpa_execution_attempts (task_id, received_at DESC);

-- statement-break

CREATE INDEX ix_rpa_execution_attempts_worker_status ON rpa_engine.rpa_execution_attempts (worker_id, status);

-- statement-break

CREATE UNIQUE INDEX uq_rpa_execution_attempts_command_id ON rpa_engine.rpa_execution_attempts (command_id) WHERE command_id IS NOT NULL;

-- statement-break

CREATE UNIQUE INDEX uq_rpa_execution_attempts_lease_id ON rpa_engine.rpa_execution_attempts (lease_id) WHERE lease_id IS NOT NULL;

-- statement-break

CREATE INDEX ix_rpa_flow_release_audits_flow_created ON rpa_engine.rpa_flow_release_audits (flow_id, created_at DESC);

-- statement-break

CREATE INDEX ix_rpa_flow_release_audits_version_created ON rpa_engine.rpa_flow_release_audits (flow_version_id, created_at DESC) WHERE flow_version_id IS NOT NULL;

-- statement-break

CREATE INDEX ix_rpa_flow_validation_status ON rpa_engine.rpa_flow_validation_runs (status, created_at);

-- statement-break

CREATE INDEX ix_rpa_flow_validation_version_created ON rpa_engine.rpa_flow_validation_runs (flow_version_id, created_at DESC);

-- statement-break

CREATE INDEX ix_rpa_callback_outbox_attempt ON rpa_engine.rpa_callback_outbox (execution_attempt_id, sequence_no);

-- statement-break

CREATE INDEX ix_rpa_callback_outbox_locked ON rpa_engine.rpa_callback_outbox (locked_at) WHERE status = 'IN_FLIGHT';

-- statement-break

CREATE INDEX ix_rpa_callback_outbox_poll ON rpa_engine.rpa_callback_outbox (next_attempt_at, created_at) WHERE status IN ('PENDING', 'RETRY');

-- statement-break

CREATE INDEX ix_rpa_callback_outbox_retention ON rpa_engine.rpa_callback_outbox (sent_at) WHERE status = 'SENT';

-- statement-break

CREATE OR REPLACE FUNCTION rpa_engine.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, rpa_engine
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$function$;

-- statement-break

CREATE OR REPLACE FUNCTION rpa_engine.guard_execution_attempt()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, rpa_engine
AS $function$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT (
            (OLD.status = 'RECEIVED'
                AND NEW.status IN ('LEASED', 'RUNNING', 'ABANDONED'))
            OR
            (OLD.status = 'LEASED'
                AND NEW.status IN ('RUNNING', 'CANCELLED', 'ABANDONED'))
            OR
            (OLD.status = 'RUNNING'
                AND NEW.status IN (
                    'SUCCESS',
                    'FAILED',
                    'WAITING_HUMAN',
                    'CANCELLED',
                    'ABANDONED'
                ))
        ) THEN
            RAISE EXCEPTION
                'Invalid execution attempt transition: % -> %',
                OLD.status,
                NEW.status;
        END IF;
    END IF;

    IF NEW.status = 'RUNNING' AND NEW.started_at IS NULL THEN
        NEW.started_at := now();
    END IF;

    IF NEW.status IN (
        'SUCCESS',
        'FAILED',
        'WAITING_HUMAN',
        'CANCELLED',
        'ABANDONED'
    ) AND NEW.ended_at IS NULL THEN
        NEW.ended_at := now();
    END IF;

    RETURN NEW;
END;
$function$;

-- statement-break

CREATE OR REPLACE FUNCTION rpa_engine.guard_flow_version()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, rpa_engine
AS $function$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT (
            (OLD.status = 'DRAFT'
                AND NEW.status IN ('VALIDATING', 'DISABLED'))
            OR
            (OLD.status = 'VALIDATING'
                AND NEW.status IN ('DRAFT', 'PUBLISHED', 'DISABLED'))
            OR
            (OLD.status = 'PUBLISHED'
                AND NEW.status IN ('DEPRECATED', 'DISABLED'))
            OR
            (OLD.status = 'DEPRECATED'
                AND NEW.status IN ('PUBLISHED', 'DISABLED'))
            OR
            (OLD.status = 'DISABLED'
                AND NEW.status = 'DRAFT')
        ) THEN
            RAISE EXCEPTION
                'Invalid Flow Version status transition: % -> %',
                OLD.status,
                NEW.status;
        END IF;
    END IF;

    IF NEW.status = 'PUBLISHED' AND NEW.published_at IS NULL THEN
        NEW.published_at := now();
    END IF;

    IF OLD.published_at IS NOT NULL
       AND ROW(
            NEW.flow_id,
            NEW.version,
            NEW.engine_type,
            NEW.entrypoint,
            NEW.manifest,
            NEW.supported_workflow_codes,
            NEW.supported_portal_types,
            NEW.input_schema,
            NEW.capabilities,
            NEW.minimum_engine_version,
            NEW.package_bucket,
            NEW.package_object_key,
            NEW.package_size_bytes,
            NEW.package_checksum_sha256,
            NEW.created_by,
            NEW.created_at,
            NEW.published_at
       ) IS DISTINCT FROM ROW(
            OLD.flow_id,
            OLD.version,
            OLD.engine_type,
            OLD.entrypoint,
            OLD.manifest,
            OLD.supported_workflow_codes,
            OLD.supported_portal_types,
            OLD.input_schema,
            OLD.capabilities,
            OLD.minimum_engine_version,
            OLD.package_bucket,
            OLD.package_object_key,
            OLD.package_size_bytes,
            OLD.package_checksum_sha256,
            OLD.created_by,
            OLD.created_at,
            OLD.published_at
       ) THEN
        RAISE EXCEPTION
            'Published Flow Version content is immutable';
    END IF;

    RETURN NEW;
END;
$function$;

-- statement-break

CREATE OR REPLACE FUNCTION rpa_engine.deny_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path TO pg_catalog, rpa_engine
AS $function$
BEGIN
    RAISE EXCEPTION
        'rpa_flow_release_audits is append-only; % is forbidden',
        TG_OP;
END;
$function$;

-- statement-break

CREATE TRIGGER trg_rpa_browser_profiles_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_browser_profiles
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_callback_outbox_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_callback_outbox
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_cdp_endpoints_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_cdp_endpoints
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_execution_attempts_10_guard
BEFORE UPDATE ON rpa_engine.rpa_execution_attempts
FOR EACH ROW EXECUTE FUNCTION rpa_engine.guard_execution_attempt();

-- statement-break

CREATE TRIGGER trg_rpa_execution_attempts_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_execution_attempts
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_flow_release_audits_no_truncate
BEFORE TRUNCATE ON rpa_engine.rpa_flow_release_audits
FOR EACH STATEMENT EXECUTE FUNCTION rpa_engine.deny_audit_mutation();

-- statement-break

CREATE TRIGGER trg_rpa_flow_release_audits_no_update_delete
BEFORE DELETE OR UPDATE ON rpa_engine.rpa_flow_release_audits
FOR EACH ROW EXECUTE FUNCTION rpa_engine.deny_audit_mutation();

-- statement-break

CREATE TRIGGER trg_rpa_flow_validation_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_flow_validation_runs
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_flow_versions_10_guard
BEFORE UPDATE ON rpa_engine.rpa_flow_versions
FOR EACH ROW EXECUTE FUNCTION rpa_engine.guard_flow_version();

-- statement-break

CREATE TRIGGER trg_rpa_flow_versions_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_flow_versions
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_flows_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_flows
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();

-- statement-break

CREATE TRIGGER trg_rpa_worker_instances_90_updated_at
BEFORE UPDATE ON rpa_engine.rpa_worker_instances
FOR EACH ROW EXECUTE FUNCTION rpa_engine.set_updated_at();
