# Agent Instructions for Web Generation Platform

## 1. 项目概述 (Project Overview)
这是一个基于 Spring Boot 的 Web 应用生成平台。
- **目的**：帮助用户快速生成 Web 应用。
- **技术栈**：Java 21, Spring Boot 4.0.8, Spring Data JPA, MySQL 8.0, Maven。

## 2. 环境与设置 (Setup & Environment)
- **JDK 版本**：Java 21
- **构建工具**：Maven
- **数据库**：MySQL 8.0，数据库名 `wgp_db`
- **本地配置文件**：`application-local.yml` 用于本地开发，**不得提交到 Git**。
- **激活本地配置**：在 `application.yml` 中设置 `spring.profiles.active: local`。

## 3. 常用命令 (Common Commands)
- **清理并编译**：`mvn clean compile`
- **打包应用**：`mvn clean package`
- **运行测试**：`mvn test`
- **本地启动**：直接运行 `WebGenerationPlatformApplication.java` 中的 `main` 方法。

## 4. 编码规范与风格 (Code Style & Conventions)
- **包结构**：遵循标准的 Spring Boot 分层架构。
    - `controller`：处理 HTTP 请求
    - `service`：业务逻辑
    - `repository`：数据访问层（JPA）
    - `entity`：数据库实体类
    - `dto`：数据传输对象
- **命名规范**：
    - 类名使用 `UpperCamelCase`（如 `UserController`）
    - 方法名和变量名使用 `lowerCamelCase`（如 `findUserById`）
    - 常量使用 `UPPER_SNAKE_CASE`（如 `MAX_RETRY_COUNT`）
- **API 设计**：尽量遵循 RESTful 风格。

## 5. 数据库与 JPA 规范 (Database & JPA Rules)
- **实体类**：使用 `@Entity` 和 `@Id` 注解，并正确配置映射关系（如 `@OneToMany`）。
- **数据源**：只能连接配置文件中指定的本地数据库 `wgp_db`。
- **禁止操作**：绝对不要修改、删除或查询 `mysql`、`sys`、`performance_schema` 等系统数据库。

## 6. 重要安全规则 (Critical Security Rules)
- **敏感信息**：`application-local.yml` 中的数据库密码等敏感信息**严禁硬编码**，必须使用环境变量 `${MYSQL_PASSWORD}` 或确保该文件被 `.gitignore` 忽略。
- **SQL 注入**：使用 JPA 或 `PreparedStatement`，禁止拼接 SQL 字符串。

## 7. Git 与提交规范 (Git & Commit Rules)
- **主分支**：`main`
- **提交信息格式**：遵循约定式提交（Conventional Commits）。
    - `feat:` 新功能
    - `fix:` 修复 Bug
    - `docs:` 文档更新
    - `chore:` 构建/工具变动
    - 示例：`feat(user): 添加用户注册接口`

## 8. AI 助手工作流 (Agent Workflow)
1.  **理解需求**：明确要修改或新增的功能。
2.  **定位代码**：根据包结构找到对应的 `controller`、`service`、`repository` 或 `entity`。
3.  **编写/修改代码**：遵循上述编码规范和 JPA 规则。
4.  **本地验证**：提供用于测试的 curl 命令或测试用例。
5.  **提交代码**：按照规范的格式得到提交信息，待用户确认后再提交。

---
*此文件为 AI 编程助手提供项目上下文，请保持更新。*