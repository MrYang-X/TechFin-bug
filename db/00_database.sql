-- ============================================================================
-- TechFin · 00 建库脚本
-- ============================================================================
-- 用途：创建数据库并设置字符集。本脚本不删除任何已有库/表。
-- 版本要求：MySQL 8.0.16+（CHECK 约束真正生效；本机实测 8.0.43）。
--
-- 执行方式（在项目根目录）：
--   mysql -u root -p --default-character-set=utf8mb4 < db/00_database.sql
--
-- 说明：
--   MySQL 中 schema 与 database 是同一个概念。本项目使用单库 techfin，
--   省份通过 dim_region.province_code 区分，不按省建库。
-- ============================================================================

SET NAMES utf8mb4;

CREATE DATABASE IF NOT EXISTS techfin
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE techfin;

-- 统一会话时区，避免 DATETIME 的 CURRENT_TIMESTAMP 随服务器时区漂移。
-- 本表全部使用 DATETIME（不是 TIMESTAMP），不受 UTC 自动换算影响。
SET time_zone = '+08:00';
