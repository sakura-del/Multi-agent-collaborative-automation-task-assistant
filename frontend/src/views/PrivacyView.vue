<script setup lang="ts">
import { ArrowLeft, Database, KeyRound, LockKeyhole, ShieldCheck } from "lucide-vue-next";
</script>

<template>
  <div class="min-h-dvh bg-[#FAF9F6] text-[#111827] dark:bg-background dark:text-foreground">
    <header class="border-b border-[#E5E7EB] bg-white/90 dark:border-border dark:bg-card/80">
      <div class="mx-auto flex h-14 max-w-4xl items-center justify-between px-4 sm:px-6">
        <RouterLink
          to="/"
          data-test="privacy-back-home"
          class="inline-flex items-center gap-1.5 text-xs font-medium text-[#4B5563] hover:text-[#111827] dark:text-muted-foreground dark:hover:text-foreground"
        >
          <ArrowLeft :size="14" aria-hidden="true" />
          返回 VeriPaper 首页
        </RouterLink>
        <RouterLink
          :to="{ name: 'terms' }"
          class="text-xs font-medium text-[#0F4C3A] hover:underline dark:text-primary"
        >
          查看用户协议 →
        </RouterLink>
      </div>
    </header>

    <main class="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
      <article class="rounded-xl border border-[#E5E7EB] bg-white p-6 sm:p-10 dark:border-border dark:bg-card">
        <p class="text-xs font-semibold tracking-wider text-[#0F4C3A] uppercase dark:text-primary">
          PRIVACY POLICY
        </p>
        <h1 data-test="privacy-title" class="mt-2 font-display text-2xl font-semibold sm:text-3xl">
          VeriPaper 隐私政策
        </h1>
        <p class="mt-2 text-xs text-[#6B7280] dark:text-muted-foreground">
          更新日期：2026 年 11 月 26 日 · 生效版本：v1.0
        </p>

        <div class="mt-6 grid gap-3 sm:grid-cols-3">
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-3.5 dark:border-border dark:bg-muted/30">
            <p class="flex items-center gap-1.5 text-xs font-semibold text-[#0F4C3A] dark:text-primary">
              <ShieldCheck :size="14" aria-hidden="true" />
              绝不用于模型训练
            </p>
            <p class="mt-1 text-xs leading-relaxed text-[#4B5563] dark:text-muted-foreground">
              您上传的文献 PDF、题录与综述草稿绝不会用于训练任何公共或私有大模型。
            </p>
          </div>
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-3.5 dark:border-border dark:bg-muted/30">
            <p class="flex items-center gap-1.5 text-xs font-semibold text-[#0F4C3A] dark:text-primary">
              <Database :size="14" aria-hidden="true" />
              私有隔离存储
            </p>
            <p class="mt-1 text-xs leading-relaxed text-[#4B5563] dark:text-muted-foreground">
              数据存储在受控云服务器与专属 PostgreSQL 库，按账号/工作空间严格隔离。
            </p>
          </div>
          <div class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-3.5 dark:border-border dark:bg-muted/30">
            <p class="flex items-center gap-1.5 text-xs font-semibold text-[#0F4C3A] dark:text-primary">
              <KeyRound :size="14" aria-hidden="true" />
              BYOK 密钥加密
            </p>
            <p class="mt-1 text-xs leading-relaxed text-[#4B5563] dark:text-muted-foreground">
              自带 API Key 在服务端对称加密存储，明文仅进不出，仅在您本人任务时调用。
            </p>
          </div>
        </div>

        <div class="mt-8 space-y-6 text-sm leading-7 text-[#374151] dark:text-foreground/90">
          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              1. 我们收集哪些信息
            </h2>
            <ul class="mt-2 list-disc space-y-1.5 pl-5">
              <li><strong>账号信息</strong>：您用于登录/注册的电子邮箱地址、用户名及第三方 OAuth（Google/GitHub）授权的唯一标识。</li>
              <li><strong>研究内容与上传文件</strong>：您主动上传至知识库的文献（PDF/Markdown/文本）、导入的参考文献题录（BibTeX/RIS）、创建的综述课题、筛选决策及导出产物。</li>
              <li><strong>模型凭据（可选）</strong>：若您选择自带模型（BYOK），我们会保存加密后的 API Key、接口 Base URL 与模型名称。</li>
              <li><strong>基础运行日志与匿名漏斗计数</strong>：用于保障服务稳定性、限流防刷与改进产品体验的匿名页面访问与任务状态汇总计数。</li>
            </ul>
          </section>

          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              2. 上传文献的存储位置与模型训练承诺
            </h2>
            <ul class="mt-2 list-disc space-y-1.5 pl-5">
              <li><strong>存储位置</strong>：您上传的所有文献原始文件、向量分块索引及结构化数据均存储于 VeriPaper 受控云服务器（独立 Docker 数据卷与 PostgreSQL 数据库），全链路采用 HTTPS/TLS 加密传输，且仅在您本人（或您显式邀请的团队工作区成员）权限范围内可读。</li>
              <li><strong>模型训练声明</strong>：我们<strong>绝不会</strong>将您上传的文献原文、未发表手稿或研究提问用于微调或训练任何大语言模型。执行综述任务时，系统仅按需把当前任务所需的检索片段发送至您选择的推理模型接口以完成文本综合与核验。</li>
            </ul>
          </section>

          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              3. 数据保留、删除与您的权利
            </h2>
            <p class="mt-2">
              您可以随时在工作台内删除已上传的知识库文档、引用库条目、Word 模板与自带 API Key 凭据；删除后对应分块与索引记录将同步清理。如需彻底注销账号并清除全部历史数据，可通过站内「意见反馈」入口或 GitHub 仓库 Issue 联系我们处理。
            </p>
          </section>

          <section class="rounded-lg border border-[#E5E7EB] bg-[#FAF9F6] p-4 text-xs text-[#4B5563] dark:border-border dark:bg-muted/30 dark:text-muted-foreground">
            <p class="flex items-center gap-1.5 font-semibold text-[#111827] dark:text-foreground">
              <LockKeyhole :size="13" aria-hidden="true" />
              联系方式
            </p>
            <p class="mt-1">
              如对本隐私政策有任何疑问，请通过工作台左下角「意见反馈」提交，或前往
              <a
                href="https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues"
                target="_blank"
                rel="noopener noreferrer"
                class="text-[#0F4C3A] underline underline-offset-2 dark:text-primary"
              >GitHub Issues</a>
              与项目维护者联系。
            </p>
          </section>
        </div>
      </article>
    </main>
  </div>
</template>
