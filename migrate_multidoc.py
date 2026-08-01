"""一次性迁移：产品单文件 → 产品多条款文件（2026-08）。

把 insurance_products.file_id 搬到新表 product_documents，并给 key_clauses 加 source_doc 列。
可重复执行（幂等）。用法：.venv\\Scripts\\python.exe migrate_multidoc.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "data" / "app.db"


def main() -> None:
    if not DB.exists():
        print("没有数据库，无需迁移（新库启动时会自动按新结构建表）")
        return
    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys=OFF")
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_documents (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES insurance_products(id) ON DELETE CASCADE,
            file_id INTEGER NOT NULL REFERENCES uploaded_files(id),
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME
        )
    """)

    cols = {r[1] for r in cur.execute("PRAGMA table_info(insurance_products)")}
    if "file_id" in cols:
        moved = cur.execute("""
            INSERT INTO product_documents (product_id, file_id, sort_order, created_at)
            SELECT p.id, p.file_id, 0, p.created_at FROM insurance_products p
            WHERE NOT EXISTS (SELECT 1 FROM product_documents d WHERE d.product_id = p.id)
        """).rowcount
        # SQLite 无法 DROP 带外键定义的列，重建表
        cur.execute("""
            CREATE TABLE insurance_products_new (
                id INTEGER PRIMARY KEY,
                status VARCHAR(20) NOT NULL,
                name VARCHAR(200),
                company VARCHAR(200),
                coverage_amount VARCHAR(500),
                deductible VARCHAR(500),
                guaranteed_renewal VARCHAR(500),
                pros_json TEXT NOT NULL,
                cons_json TEXT NOT NULL,
                analysis_json TEXT,
                is_shortlisted BOOLEAN NOT NULL,
                shortlisted_at DATETIME,
                created_at DATETIME
            )
        """)
        cur.execute("""
            INSERT INTO insurance_products_new
            SELECT id, status, name, company, coverage_amount, deductible, guaranteed_renewal,
                   pros_json, cons_json, analysis_json, is_shortlisted, shortlisted_at, created_at
            FROM insurance_products
        """)
        cur.execute("DROP TABLE insurance_products")
        cur.execute("ALTER TABLE insurance_products_new RENAME TO insurance_products")
        print(f"已迁移 {moved} 个产品的条款文件到 product_documents，并重建 insurance_products 表")
    else:
        print("insurance_products.file_id 已不存在，跳过")

    clause_cols = {r[1] for r in cur.execute("PRAGMA table_info(key_clauses)")}
    if "source_doc" not in clause_cols:
        cur.execute("ALTER TABLE key_clauses ADD COLUMN source_doc VARCHAR(255)")
        print("已为 key_clauses 添加 source_doc 列")
    else:
        print("key_clauses.source_doc 已存在，跳过")

    con.commit()
    con.close()
    print("迁移完成")


if __name__ == "__main__":
    main()
