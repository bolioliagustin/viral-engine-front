const Database = require('better-sqlite3');
const path = require('path');

const dbPath = path.join(__dirname, '..', '..', 'data', 'app.db');
const db = new Database(dbPath);

// Enable WAL mode for better concurrency with Python worker
db.pragma('journal_mode = WAL');

// Initialize tables
db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    subscription_status TEXT DEFAULT 'free'
  );

  CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    video_url TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
  );

  CREATE TABLE IF NOT EXISTS content_results (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    type TEXT CHECK(type IN ('twitter_thread', 'linkedin_post', 'short_script', 'summary')),
    content TEXT,
    metadata TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
  );

  CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
  CREATE INDEX IF NOT EXISTS idx_content_results_job_id ON content_results(job_id);
`);

// Prepared statements for better performance
const statements = {
  createJob: db.prepare(`
    INSERT INTO jobs (id, user_id, video_url, status)
    VALUES (?, ?, ?, 'pending')
  `),
  
  getJobById: db.prepare(`
    SELECT * FROM jobs WHERE id = ?
  `),
  
  getJobWithResults: db.prepare(`
    SELECT 
      j.*,
      json_group_array(
        json_object(
          'id', cr.id,
          'type', cr.type,
          'content', cr.content,
          'metadata', cr.metadata
        )
      ) as results
    FROM jobs j
    LEFT JOIN content_results cr ON j.id = cr.job_id
    WHERE j.id = ?
    GROUP BY j.id
  `),
  
  updateJobStatus: db.prepare(`
    UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
  `),
  
  updateJobError: db.prepare(`
    UPDATE jobs SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
  `)
};

module.exports = {
  db,
  statements
};
