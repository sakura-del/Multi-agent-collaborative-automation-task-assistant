<script setup lang="ts">
import { ArrowLeft } from "lucide-vue-next";
</script>

<template>
  <div class="min-h-dvh bg-[#FAF9F6] text-[#111827] dark:bg-background dark:text-foreground">
    <header class="border-b border-[#E5E7EB] bg-white/90 dark:border-border dark:bg-card/80">
      <div class="mx-auto flex h-14 max-w-4xl items-center justify-between px-4 sm:px-6">
        <RouterLink
          to="/"
          data-test="terms-back-home"
          class="inline-flex items-center gap-1.5 text-xs font-medium text-[#4B5563] hover:text-[#111827] dark:text-muted-foreground dark:hover:text-foreground"
        >
          <ArrowLeft :size="14" aria-hidden="true" />
          返回 VeriPaper 首页
        </RouterLink>
        <RouterLink
          :to="{ name: 'privacy' }"
          class="text-xs font-medium text-[#0F4C3A] hover:underline dark:text-primary"
        >
          查看隐私政策 →
        </RouterLink>
      </div>
    </header>

    <main class="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
      <article class="rounded-xl border border-[#E5E7EB] bg-white p-6 sm:p-10 dark:border-border dark:bg-card">
        <p class="text-xs font-semibold tracking-wider text-[#0F4C3A] uppercase dark:text-primary">
          TERMS OF SERVICE
        </p>
        <h1 data-test="terms-title" class="mt-2 font-display text-2xl font-semibold sm:text-3xl">
          VeriPaper 用户协议
        </h1>
        <p class="mt-2 text-xs text-[#6B7280] dark:text-muted-foreground">
          更新日期：2026 年 11 月 26 日 · 生效版本：v1.0
        </p>

        <div class="mt-8 space-y-6 text-sm leading-7 text-[#374151] dark:text-foreground/90">
          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              1. 服务说明与学术合规责任
            </h2>
            <p class="mt-2">
              VeriPaper 是面向学术研究者的文献综述辅助工作台，提供多库检索、PRISMA 筛选、结构化证据提取与引文核验功能。系统输出的综述正文、核验结论与参考文献格式仅供科研辅助参考，<strong>最终公开发表或提交的学术文本、引用准确性与学术伦理责任由使用者本人审核并承担</strong>。
            </p>
          </section>

          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              2. 账号与额度规则
            </h2>
            <ul class="mt-2 list-disc space-y-1.5 pl-5">
              <li>新邮箱通过验证码或一键登录链接验证后即自动创建账号，可享受免费版每月 3 篇平台综述体验额度（单次任务文献数上限 15 篇）；使用自带模型（BYOK）默认不消耗平台篇数额度。</li>
              <li>请妥善保管您的登录凭据与开放 API Key，不得利用自动化脚本恶意刷取平台免费算力或绕过限流保护；如发现批量滥用行为，平台有权限制或暂停相关账号。</li>
            </ul>
          </section>

          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              3. 内容归属与知识产权
            </h2>
            <p class="mt-2">
              您上传的文献、个人笔记以及通过 VeriPaper 辅助生成的综述内容，其知识产权归您（或原文献版权方）所有；您应确保上传至知识库的文献具有合法的个人学习研究使用权，不得利用本服务传播侵权或违法内容。
            </p>
          </section>

          <section>
            <h2 class="text-base font-semibold text-[#111827] dark:text-foreground">
              4. 服务可用性与条款更新
            </h2>
            <p class="mt-2">
              第三方学术检索接口（如 arXiv、PubMed、OpenAlex、Semantic Scholar、Crossref）及外部大模型 API 可能受上游限流或网络波动影响，我们将尽力提供降级与重试保护，但不对第三方服务的连续可用性作绝对担保。协议如有重大更新，将在页面公布更新日期。
            </p>
          </section>
        </div>
      </article>
    </main>
  </div>
</template>
