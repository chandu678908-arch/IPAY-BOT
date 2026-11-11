const serviceUrl = Deno.env.get("SUPABASE_URL")!;
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const telegramToken = Deno.env.get("TELEGRAM_BOT_TOKEN")!;
const telegramChatId = Deno.env.get("REPORT_CHAT_ID")!;
const webhookSecret = Deno.env.get("DAILY_REPORT_WEBHOOK_SECRET")!;
const reportTimezone = Deno.env.get("REPORT_TIMEZONE") ?? "Asia/Kolkata";
const headers = { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` };

function localParts(date: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  return Object.fromEntries(parts.map((p) => [p.type, p.value]));
}

function localDate(date: Date, timeZone: string): string {
  const p = localParts(date, timeZone);
  return `${p.year}-${p.month}-${p.day}`;
}

function addDays(day: string, amount: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + amount);
  return d.toISOString().slice(0, 10);
}

function localMidnightUtc(day: string, timeZone: string): Date {
  const [year, month, date] = day.split("-").map(Number);
  const target = Date.UTC(year, month - 1, date);
  let guess = target;
  for (let i = 0; i < 4; i++) {
    const p = localParts(new Date(guess), timeZone);
    const represented = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour), Number(p.minute));
    const delta = target - represented;
    if (!delta) break;
    guess += delta;
  }
  return new Date(guess);
}

async function tablePage(table: string, query: URLSearchParams, offset: number, pageSize = 1000) {
  query.set("limit", String(pageSize));
  const response = await fetch(`${serviceUrl}/rest/v1/${table}?${query}`, {
    headers: { ...headers, Range: `${offset}-${offset + pageSize - 1}`, Prefer: "count=none" },
  });
  if (!response.ok) throw new Error(`${table} read failed (${response.status}): ${await response.text()}`);
  return await response.json();
}

async function allRows(table: string, query: URLSearchParams) {
  const rows: any[] = [];
  for (let offset = 0; ; offset += 1000) {
    const page = await tablePage(table, new URLSearchParams(query), offset);
    rows.push(...page);
    if (page.length < 1000) return rows;
  }
}

async function saveReport(report: any, deliveryStatus: string, errorText?: string, incrementAttempt = false) {
  let deliveryAttempts = 0;
  const currentQuery = new URLSearchParams({
    select: "delivery_attempts", report_date: `eq.${report.date}`,
    timezone: `eq.${report.timezone}`, limit: "1",
  });
  const currentResponse = await fetch(`${serviceUrl}/rest/v1/daily_reports?${currentQuery}`, { headers });
  if (!currentResponse.ok) throw new Error("Could not read prior report delivery status");
  const current = await currentResponse.json();
  deliveryAttempts = Number(current[0]?.delivery_attempts ?? 0) + (incrementAttempt ? 1 : 0);
  const result = await fetch(`${serviceUrl}/rest/v1/daily_reports?on_conflict=report_date,timezone`, {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json", Prefer: "resolution=merge-duplicates,return=minimal" },
    body: JSON.stringify({
      report_date: report.date, timezone: report.timezone, report_json: report,
      delivery_status: deliveryStatus,
      delivery_attempts: deliveryAttempts,
      delivered_at: deliveryStatus === "sent" ? new Date().toISOString() : null,
      last_error: errorText?.slice(0, 1000) ?? null,
    }),
  });
  if (!result.ok) throw new Error(`daily_reports write failed (${result.status}): ${await result.text()}`);
}

async function alreadySent(day: string): Promise<boolean> {
  const query = new URLSearchParams({
    select: "delivery_status", report_date: `eq.${day}`,
    timezone: `eq.${reportTimezone}`, limit: "1",
  });
  const response = await fetch(`${serviceUrl}/rest/v1/daily_reports?${query}`, { headers });
  if (!response.ok) throw new Error(`daily report status read failed (${response.status})`);
  const rows = await response.json();
  return rows[0]?.delivery_status === "sent";
}

async function reportStatus(day: string): Promise<string | null> {
  const query = new URLSearchParams({ select: "delivery_status", report_date: `eq.${day}`, timezone: `eq.${reportTimezone}`, limit: "1" });
  const response = await fetch(`${serviceUrl}/rest/v1/daily_reports?${query}`, { headers });
  if (!response.ok) throw new Error(`daily report status read failed (${response.status})`);
  const rows = await response.json();
  return rows[0]?.delivery_status ?? null;
}

function csvEscape(value: unknown): string {
  const raw = value == null ? "" : typeof value === "string" ? value : JSON.stringify(value);
  return `"${raw.replaceAll('"', '""')}"`;
}

async function telegram(method: string, body: BodyInit, json = false) {
  const response = await fetch(`https://api.telegram.org/bot${telegramToken}/${method}`, {
    method: "POST", headers: json ? { "Content-Type": "application/json" } : undefined, body,
  });
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(`Telegram ${method} failed: ${JSON.stringify(result).slice(0, 1200)}`);
  return result;
}

async function sendDocument(name: string, content: string, caption?: string) {
  const form = new FormData();
  form.append("chat_id", telegramChatId);
  if (caption) form.append("caption", caption.slice(0, 900));
  form.append("document", new Blob([content], { type: "application/octet-stream" }), name);
  await telegram("sendDocument", form);
}

async function buildAndDeliver(day: string) {
  if (await alreadySent(day)) return { day, already_sent: true };
  const start = localMidnightUtc(day, reportTimezone);
  const end = localMidnightUtc(addDays(day, 1), reportTimezone);
  const questions = await allRows("question_events", new URLSearchParams({
    select: "id,user_id,username,display_name,chat_id,chat_type,chat_title,message_id,original_message,language,bot_response,answer_status,classification_confidence,category,subcategory,intent,problem_summary,priority,sentiment,is_complaint,is_feature_request,created_at,updated_at",
    created_at: `gte.${start.toISOString()}`,
    and: `(created_at.lt.${end.toISOString()})`,
    order: "created_at.asc,id.asc",
  }));
  const activity = await allRows("bot_activity_logs", new URLSearchParams({
    select: "id", event_type: "eq.incoming_message", created_at: `gte.${start.toISOString()}`,
    and: `(created_at.lt.${end.toISOString()})`, order: "created_at.asc,id.asc",
  }));
  const links: any[] = [];
  for (let i = 0; i < questions.length; i += 100) {
    const ids = questions.slice(i, i + 100).map((q) => q.id);
    if (!ids.length) continue;
    const query = new URLSearchParams({ select: "question_id,cluster_id", question_id: `in.(${ids.join(",")})` });
    const response = await fetch(`${serviceUrl}/rest/v1/question_cluster_links?${query}`, { headers });
    if (!response.ok) throw new Error(`question links read failed (${response.status})`);
    links.push(...await response.json());
  }
  const clusterIds = [...new Set(links.map((link) => link.cluster_id))];
  const clusters: any[] = [];
  for (let i = 0; i < clusterIds.length; i += 100) {
    const query = new URLSearchParams({ select: "id,title,category,occurrence_count,unique_user_count,status,priority", id: `in.(${clusterIds.slice(i, i + 100).join(",")})` });
    const response = await fetch(`${serviceUrl}/rest/v1/problem_clusters?${query}`, { headers });
    if (!response.ok) throw new Error(`problem clusters read failed (${response.status})`);
    clusters.push(...await response.json());
  }
  const clusterByQuestion = new Map(links.map((link) => [link.question_id, link.cluster_id]));
  const clusterById = new Map(clusters.map((cluster) => [cluster.id, cluster]));
  for (const question of questions) {
    const clusterId = clusterByQuestion.get(question.id);
    question.problem_cluster_id = clusterId ?? null;
    question.problem_cluster = clusterId ? clusterById.get(clusterId)?.title ?? null : null;
  }

  const count = (key: string) => questions.reduce((acc, q) => (acc[q[key] ?? "unknown"] = (acc[q[key] ?? "unknown"] ?? 0) + 1, acc), {} as Record<string, number>);
  const groups = new Map<string, number>();
  const repeated = new Map<string, { title: string; count: number; userIds: Set<string> }>();
  for (const q of questions) {
    if (["group", "supergroup"].includes(q.chat_type)) {
      const name = q.chat_title || String(q.chat_id);
      groups.set(name, (groups.get(name) ?? 0) + 1);
    }
    const clusterId = q.problem_cluster_id;
    if (clusterId) {
      const current = repeated.get(clusterId) ?? { title: q.problem_cluster || q.problem_summary || "Other", count: 0, userIds: new Set<string>() };
      current.count++;
      if (q.user_id != null) current.userIds.add(String(q.user_id));
      repeated.set(clusterId, current);
    }
  }
  const topProblems = [...repeated.values()].map((item) => ({ title: item.title, count: item.count, unique_users: item.userIds.size })).sort((a, b) => b.count - a.count).slice(0, 25);
  const statuses = count("answer_status");
  const unresolvedQuestions = questions.filter((q) => ["unanswered", "partially_answered", "needs_human_review"].includes(q.answer_status));
  const gapCounts = new Map<string, number>();
  for (const q of unresolvedQuestions) {
    const key = `${q.category ?? "other"}|||${q.problem_summary ?? "unknown"}`;
    gapCounts.set(key, (gapCounts.get(key) ?? 0) + 1);
  }
  const report = {
    date: day, timezone: reportTimezone, total_incoming_messages: activity.length,
    total_questions: questions.length, unanswered_count: statuses.unanswered ?? 0,
    partially_answered_count: statuses.partially_answered ?? 0, resolved_count: statuses.resolved ?? 0,
    unique_users: new Set(questions.map((q) => q.user_id).filter((x) => x != null)).size,
    groups_involved: groups.size,
    complaints: questions.filter((q) => q.is_complaint).length,
    feature_requests: questions.filter((q) => q.is_feature_request).length,
    by_category: count("category"), by_status: statuses,
    top_problems: topProblems,
    top_groups: [...groups.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25),
    unanswered: unresolvedQuestions,
    all_questions: questions,
    knowledge_gaps: [...gapCounts.entries()].map(([key, occurrences]) => { const [category, summary] = key.split("|||"); return { category, summary, occurrences }; }).sort((a, b) => b.occurrences - a.occurrences).slice(0, 30),
    recommended_actions: [...new Set(questions.filter((q) => ["unanswered", "needs_human_review"].includes(q.answer_status)).map((q) => `Review knowledge gap: ${q.category} — ${q.problem_summary}`))].slice(0, 30),
  };
  await saveReport(report, "pending", undefined, true);
  const summary = `iPAY Daily Support Report · ${day} (${reportTimezone})\nIncoming: ${activity.length} | Questions: ${questions.length}\nUnanswered: ${report.unanswered_count} | Partial: ${report.partially_answered_count} | Resolved: ${report.resolved_count}\nComplaints: ${report.complaints} | Feature requests: ${report.feature_requests}\nUnique users: ${report.unique_users} | Groups: ${report.groups_involved}\nFull individual questions and bot answers are attached.`;
  try {
    await telegram("sendMessage", JSON.stringify({ chat_id: telegramChatId, text: summary }), true);
    const csvHeaders = ["id", "created_at", "user_id", "username", "chat_type", "chat_title", "category", "priority", "answer_status", "intent", "problem_summary", "problem_cluster", "original_message", "bot_response"];
    const csvRecords = [csvHeaders.map(csvEscape).join(","), ...questions.map((q) => [q.id,q.created_at,q.user_id,q.username,q.chat_type,q.chat_title,q.category,q.priority,q.answer_status,q.intent,q.problem_summary,q.problem_cluster,q.original_message,q.bot_response].map(csvEscape).join(","))];
    const fullJson = JSON.stringify(report, null, 2);
    const fullCsv = csvRecords.join("\r\n");
    const maxChars = 30_000_000;
    if (fullJson.length <= maxChars && fullCsv.length <= maxChars) {
      await sendDocument(`ipay-report-${day}.json`, fullJson);
      await sendDocument(`ipay-report-${day}.csv`, fullCsv);
    } else {
      for (let startIndex = 0, part = 1; startIndex < questions.length; startIndex += 3000, part++) {
        const rows = questions.slice(startIndex, startIndex + 3000);
        const partReport = { date: day, timezone: reportTimezone, part, question_count: rows.length, all_questions: rows };
        await sendDocument(`ipay-report-${day}-part-${part}.json`, JSON.stringify(partReport));
        const csvPart = [csvRecords[0], ...csvRecords.slice(startIndex + 1, startIndex + 1 + rows.length)].join("\r\n");
        await sendDocument(`ipay-report-${day}-part-${part}.csv`, csvPart);
      }
    }
    await saveReport(report, "sent");
  } catch (error) {
    await saveReport(report, "failed", String(error));
    throw error;
  }
  return { day, total_questions: questions.length, sent: true };
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
  if (!serviceUrl || !serviceKey || !telegramToken || !telegramChatId || !webhookSecret) {
    return Response.json({ error: "Missing report function secrets" }, { status: 500 });
  }
  if (request.headers.get("x-report-secret") !== webhookSecret) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const body = await request.json().catch(() => ({}));
    const today = localDate(new Date(), reportTimezone);
    const nowParts = localParts(new Date(), reportTimezone);
    let targetDay = typeof body.date === "string" ? body.date : undefined;
    if (targetDay && !/^\d{4}-\d{2}-\d{2}$/.test(targetDay)) {
      return Response.json({ error: "date must use YYYY-MM-DD" }, { status: 400 });
    }
    if (!targetDay) {
      const minutes = Number(nowParts.hour) * 60 + Number(nowParts.minute);
      if (minutes >= 1439) targetDay = today;
      else {
        const yesterday = addDays(today, -1);
        const previousStatus = await reportStatus(yesterday);
        if (!previousStatus || previousStatus === "sent") return Response.json({ skipped: true, reason: "not report time" });
        targetDay = yesterday;
      }
    }
    const result = await buildAndDeliver(targetDay);
    return Response.json(result);
  } catch (error) {
    console.error("daily question report failed", error);
    return Response.json({ error: String(error) }, { status: 500 });
  }
});
