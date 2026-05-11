CREATE TABLE IF NOT EXISTS projects (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    path            TEXT NOT NULL,
    repo_url        TEXT,
    cloned_at       TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    last_indexed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id),
    started_at  TIMESTAMP DEFAULT NOW(),
    ended_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS queries (
    id                     SERIAL PRIMARY KEY,
    session_id             INTEGER REFERENCES sessions(id),
    query_text             TEXT NOT NULL,
    response_text          TEXT,
    deepseek_input_tokens  INTEGER DEFAULT 0,
    deepseek_output_tokens INTEGER DEFAULT 0,
    deepseek_cost_usd      NUMERIC(10, 6) DEFAULT 0,
    created_at             TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS indexed_files (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id),
    file_path   TEXT NOT NULL,
    file_size   INTEGER,
    indexed_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blocked_attempts (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER REFERENCES sessions(id),
    attempted_path  TEXT NOT NULL,
    reason          VARCHAR(255),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_queries_session ON queries(session_id);
CREATE INDEX IF NOT EXISTS idx_queries_created ON queries(created_at);
CREATE INDEX IF NOT EXISTS idx_indexed_files_project ON indexed_files(project_id);
CREATE INDEX IF NOT EXISTS idx_blocked_attempts_session ON blocked_attempts(session_id);
