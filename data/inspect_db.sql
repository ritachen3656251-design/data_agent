-- 在 Postgres 中执行，检查 ub schema 的表结构与数据填充
-- 用法: psql -h 127.0.0.1 -U postgres -d tianchi_ub -f inspect_db.sql
-- 若某列不存在会报错，可注释掉对应查询后重试

\echo '========== ub schema 表列表 =========='
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema = 'ub'
ORDER BY table_name;

\echo ''
\echo '========== ub.daily_metrics 结构 =========='
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'ub' AND table_name = 'daily_metrics'
ORDER BY ordinal_position;

\echo ''
\echo '========== ub.daily_metrics 行数与日期范围 =========='
SELECT COUNT(*) AS row_count FROM ub.daily_metrics;
SELECT MIN(dt)::text AS min_dt, MAX(dt)::text AS max_dt FROM ub.daily_metrics;

\echo ''
\echo '========== ub.daily_metrics 各列非空统计 =========='
SELECT
  COUNT(*) AS total,
  COUNT(dt) AS dt_cnt,
  COUNT(pv) AS pv_cnt,
  COUNT(uv) AS uv_cnt,
  COUNT(buyers) AS buyers_cnt,
  COUNT(cart_users) AS cart_users_cnt
FROM ub.daily_metrics;

\echo ''
\echo '========== ub.daily_metrics 样本(前3行) =========='
SELECT * FROM ub.daily_metrics ORDER BY dt DESC LIMIT 3;

\echo ''
\echo '========== ub.user_behavior 结构 =========='
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'ub' AND table_name = 'user_behavior'
ORDER BY ordinal_position;

\echo ''
\echo '========== ub.user_behavior 行数与日期范围 =========='
SELECT COUNT(*) AS row_count FROM ub.user_behavior;
SELECT MIN(dt)::text AS min_dt, MAX(dt)::text AS max_dt FROM ub.user_behavior;

\echo ''
\echo '========== ub.user_behavior 各列非空统计 =========='
SELECT
  COUNT(*) AS total,
  COUNT(user_id) AS user_id_cnt,
  COUNT(dt) AS dt_cnt,
  COUNT(behavior_type) AS behavior_type_cnt,
  COUNT(category_id) AS category_id_cnt
FROM ub.user_behavior;

\echo ''
\echo '========== ub.user_behavior behavior_type 分布 =========='
SELECT behavior_type, COUNT(*) AS cnt
FROM ub.user_behavior
GROUP BY behavior_type
ORDER BY cnt DESC;

\echo ''
\echo '========== ub.user_behavior 样本(前3行) =========='
SELECT * FROM ub.user_behavior LIMIT 3;
