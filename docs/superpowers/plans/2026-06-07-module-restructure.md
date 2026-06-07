# 前端子模块重构方案

> **Goal:** 重新组织 Admin 前端模块结构，拆分 Dashboard 为 3 个独立模块，调整菜单顺序。

**Architecture:** 新增 2 个页面文件（MarketCenter, TradingCenter），修改 6 个现有页面，更新 App.tsx 路由 + 所有跨页面跳转链接。

---

## 页面映射

| 现在 | 重构后 |
|------|--------|
| `/dashboard` (7 tab) | `/market` (新建) + `/board` (实验看板) + `/trade` (新建) |
| `/experiments` | `/lab` (实验管理，去 Prod) |
| `/data` | `/data` (数据中心，加 Pipeline/Alert/概览) |
| `/logs` | `/logs` (日志中心，改名) |
| `/cron` | `/cron` (调度中心，改名) |

## 菜单顺序

行情中心 → 交易中心 → 实验看板 → 实验管理 → 模型&策略 → 数据中心 → 日志中心 → 调度中心

---

## Task 1: 新建 MarketCenter 页面

**Files:** Create `pages/MarketCenter.tsx`

行情中心，US/HK 两个主 tab，每个下含 Index/Stock 子 tab。
指数 tab：每指数两个图（5m + 日线）。个股 tab：同上 + 股票选择器。

## Task 2: 新建 TradingCenter 页面

**Files:** Create `pages/TradingCenter.tsx`

4 个子 tab：量化看板 (← DashboardProd), 量化交易 (← LabProd), 量化配置 (← ConfigProd), 手动交易 (placeholder), 交易账户 (placeholder)

## Task 3: 修改 App.tsx 路由 + 菜单

**Files:** Modify `App.tsx`

新路由表 + 菜单项 + 顺序调整

## Task 4: 修改 Dashboard → 实验看板

**Files:** Modify `pages/Dashboard.tsx`

删 Overview/Prod/Pipeline/Alerts tab，保留 Live/Paper/Debug，修复 Live run 下拉框

## Task 5: 修改 Experiments → 实验管理

**Files:** Modify `pages/Experiments.tsx`

删 Configs Prod / Lab Prod 子 tab

## Task 6: 修改 DataMap → 数据中心

**Files:** Modify `pages/DataMap.tsx`

加 Pipeline/Alert/数据概览 tab，修复 ws_collector 配额显示

## Task 7: 更新所有跨页面跳转链接

**Files:** Modify `Experiments.tsx` (详情→Dashboard 跳转), `CronJobs.tsx`, `Models.tsx` 等

同步所有 `navigate()` 调用中的路径

## Task 8: 构建部署
