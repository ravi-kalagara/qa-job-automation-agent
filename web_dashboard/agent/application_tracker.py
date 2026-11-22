import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import os

class ApplicationTracker:
    def __init__(self, db_path: str = "data/applications.db"):
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
    
    def _create_tables(self):
        """Create necessary database tables"""
        self.conn.executescript('''
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE,
                job_title TEXT,
                company TEXT,
                platform TEXT,
                application_url TEXT,
                match_score REAL DEFAULT 0.0,
                application_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'applied',
                cover_letter TEXT,
                resume_version TEXT,
                response_date DATETIME,
                notes TEXT,
                follow_up_count INTEGER DEFAULT 0,
                last_follow_up DATETIME
            );
            
            CREATE TABLE IF NOT EXISTS application_stats (
                date DATE PRIMARY KEY,
                applications_sent INTEGER DEFAULT 0,
                responses_received INTEGER DEFAULT 0,
                interviews_scheduled INTEGER DEFAULT 0,
                rejections INTEGER DEFAULT 0,
                offers INTEGER DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company TEXT,
                job_id TEXT,
                reason TEXT,
                date_added DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                scheduled_time DATETIME NOT NULL,
                application_id INTEGER,
                template_type TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            );
            
            CREATE TABLE IF NOT EXISTS networking_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER,
                person_name TEXT,
                person_title TEXT,
                person_type TEXT,
                company TEXT,
                profile_url TEXT,
                action TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        self.conn.commit()
    
    def record_application(self, job: Dict, status: str = "applied"):
        """Record a new application"""
        try:
            self.conn.execute('''
                INSERT OR REPLACE INTO applications 
                (job_id, job_title, company, platform, application_url, 
                 match_score, application_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                job.get('id', job.get('link', str(datetime.now().timestamp()))),
                job.get('title', job.get('job_title', 'Unknown')),
                job.get('company', 'Unknown'),
                job.get('platform', 'Unknown'),
                job.get('link', job.get('application_url', '')),
                job.get('match_score', 0.0),
                datetime.now(),
                status
            ))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"Error recording application: {e}")
            return False
    
    def update_status(self, job_id: str, new_status: str, notes: str = ""):
        """Update application status"""
        self.conn.execute('''
            UPDATE applications 
            SET status = ?, 
                response_date = CASE
                    WHEN ? IN ('responded', 'interview', 'rejected', 'offer', 'offered')
                    THEN CURRENT_TIMESTAMP
                    ELSE response_date 
                END,
                notes = CASE 
                    WHEN notes IS NULL OR notes = '' 
                    THEN ? 
                    ELSE notes || CHAR(10) || ? 
                END
            WHERE job_id = ? OR id = ?
        ''', (new_status, new_status, notes, notes, job_id, job_id))
        self.conn.commit()
    
    def get_stats(self, days: int = 30) -> Dict:
        """Get application statistics"""
        cursor = self.conn.execute('''
            SELECT
                COUNT(*) as total_applications,
                COALESCE(SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END), 0) as active_applications,
                COALESCE(SUM(CASE WHEN status = 'interview' THEN 1 ELSE 0 END), 0) as interviews,
                COALESCE(SUM(CASE WHEN status IN ('offer', 'offered') THEN 1 ELSE 0 END), 0) as offers,
                COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0) as rejections,
                COALESCE(SUM(CASE WHEN status IN ('interview', 'offer', 'offered') THEN 1 ELSE 0 END), 0) as responses_received
            FROM applications
            WHERE application_date >= datetime('now', '-' || ? || ' days')
        ''', (days,))

        row = cursor.fetchone()
        total = row['total_applications'] or 0
        responses = row['responses_received'] or 0
        interviews = row['interviews'] or 0
        offers = row['offers'] or 0

        return {
            'total_applications': total,
            'active_applications': row['active_applications'] or 0,
            'interviews': interviews,
            'offers': offers,
            'rejections': row['rejections'] or 0,
            'response_rate': responses / total if total > 0 else 0,
            'interview_rate': interviews / total if total > 0 else 0,
            'offer_rate': offers / total if total > 0 else 0
        }

    def get_timeline(self, days: int = 30) -> List[Dict]:
        """Applications per day for the last `days` days (zero-filled)."""
        cursor = self.conn.execute('''
            SELECT date(application_date) as day, COUNT(*) as count
            FROM applications
            WHERE application_date >= datetime('now', '-' || ? || ' days')
            GROUP BY date(application_date)
            ORDER BY day
        ''', (days,))
        counts = {row['day']: row['count'] for row in cursor.fetchall()}
        today = datetime.now().date()
        timeline = []
        for offset in range(days - 1, -1, -1):
            day = today - timedelta(days=offset)
            timeline.append({'date': day.isoformat(), 'count': counts.get(day.isoformat(), 0)})
        return timeline

    def get_setting(self, key: str, default=None):
        row = self.conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        self.conn.execute('''
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        ''', (key, value))
        self.conn.commit()

    def get_all_settings(self) -> Dict:
        rows = self.conn.execute('SELECT key, value FROM settings').fetchall()
        return {row['key']: row['value'] for row in rows}
    
    def has_applied(self, job_url: str) -> bool:
        """Check if already applied to a job"""
        cursor = self.conn.execute(
            'SELECT COUNT(*) as count FROM applications WHERE application_url = ?',
            (job_url,)
        )
        return cursor.fetchone()['count'] > 0
    
    def get_applications_for_follow_up(self) -> List[Dict]:
        """Get applications that need follow-up"""
        cursor = self.conn.execute('''
            SELECT * FROM applications 
            WHERE status IN ('applied', 'viewed', 'interview_completed')
            AND application_date >= datetime('now', '-30 days')
            AND (
                last_follow_up IS NULL 
                OR last_follow_up <= datetime('now', '-5 days')
            )
            ORDER BY application_date DESC
        ''')
        return [dict(row) for row in cursor.fetchall()]
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
