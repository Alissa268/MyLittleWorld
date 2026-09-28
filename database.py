import os
import pyodbc
from dotenv import load_dotenv
load_dotenv()

DB_DRIVER   = os.getenv("DB_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER   = os.getenv("DB_SERVER", "100.64.116.35,1433")
DB_NAME     = os.getenv("DB_NAME", "MediChainDB")
DB_USER     = os.getenv("DB_USER", "sa")
DB_TRUST_CERT = os.getenv("DB_TRUST_CERT", "yes")


def get_db_connection():
    db_password = os.getenv("DB_PASSWORD")
    return pyodbc.connect(
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={db_password};"
        "Encrypt=yes;"
        f"TrustServerCertificate={DB_TRUST_CERT};"
        "Connection Timeout=30;"
    )
 

_dept_with_category_cache = None

def get_departments_with_category() -> list[dict]:

    global _dept_with_category_cache
    if _dept_with_category_cache is not None:
        return _dept_with_category_cache
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT dc.category_id, dc.name AS parent, d.dept_id, d.name AS child
            FROM DepartmentCategory dc
            JOIN Department d ON d.category_id = dc.category_id
            ORDER BY dc.name, d.name
        """)
        rows = cursor.fetchall()
        conn.close()
        _dept_with_category_cache = [{
            "category_id": row[0],
            "parentDept": row[1],
            "dept_id": row[2],
            "childDept": row[3],
        } for row in rows]
        print(f"科別清單（含父科別）載入完成（共 {len(_dept_with_category_cache)} 個子科別）")
        return _dept_with_category_cache
    except Exception as e:
        print(f"載入科別（含父科別）失敗：{e}")
        return []


def get_available_schedules(dept_name: str, from_date: str,
                            visit_type: str | None = None) -> list[dict]:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        sql = """
            SELECT doc.name, d.name, s.date, s.session, s.room, doc.specialty_tags
            FROM Schedule s
            JOIN Doctor doc ON doc.doctor_id = s.doctor_id
            JOIN Department d ON d.dept_id = s.dept_id
            WHERE d.name = ?
              AND s.date >= ?
              AND s.status IS NULL
              AND doc.is_placeholder = 0
        """
        params = [dept_name, from_date]

        if visit_type:
            sql += "              AND s.visit_type = ?\n"
            params.append(visit_type)

        sql += "            ORDER BY s.date, s.session"
        cursor.execute(sql, *params)
        rows = cursor.fetchall()
        conn.close()
        return [{
            "doctor": row[0],
            "childDept": row[1],
            "date": str(row[2]),
            "session": row[3],
            "room": row[4],
            "specialty_tags": row[5] or "",
        } for row in rows]
    except Exception as e:
        print(f"查詢班表失敗：{e}")
        return []
