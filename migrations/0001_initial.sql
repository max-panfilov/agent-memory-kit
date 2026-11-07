CREATE TABLE IF NOT EXISTS projects (
    id integer PRIMARY KEY CHECK (id > 0),
    name text NOT NULL,
    aliases jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS applications (
    project integer NOT NULL REFERENCES projects(id),
    id text NOT NULL,
    name text NOT NULL,
    PRIMARY KEY (project, id)
);

CREATE TABLE IF NOT EXISTS project_events (
    id uuid PRIMARY KEY,
    project integer NOT NULL,
    application_id text NOT NULL,
    event_type text NOT NULL,
    title text NOT NULL,
    summary text NOT NULL,
    details text NOT NULL,
    event_status text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    source_refs jsonb NOT NULL,
    supersedes_id uuid REFERENCES project_events(id),
    payload_hash text NOT NULL CHECK (payload_hash ~ '^[a-f0-9]{64}$'),
    shared_safe boolean NOT NULL CHECK (shared_safe IS TRUE),
    canonical_payload jsonb NOT NULL,
    FOREIGN KEY (project, application_id) REFERENCES applications(project, id)
);

CREATE INDEX IF NOT EXISTS project_events_scope_order
    ON project_events (project, application_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS project_events_supersedes
    ON project_events (supersedes_id) WHERE supersedes_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_audit (
    event_id uuid PRIMARY KEY REFERENCES project_events(id),
    actor text NOT NULL,
    payload_hash text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
