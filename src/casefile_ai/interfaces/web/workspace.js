"use strict";
const names = ["materials", "qa", "claims", "conflicts", "timeline"];
const providerMessages = {
  authentication_error: "模型凭据无效，请检查 API key。",
  permission_error: "没有访问该模型的权限，请检查模型配置。",
  rate_limited: "模型服务请求受限，请稍后重试并检查配额。",
  timeout: "模型服务响应超时，请稍后重试。",
  unavailable: "模型服务暂不可用，请稍后重试。",
  connection_error: "无法连接模型服务，请检查网络和服务地址。",
  invalid_response: "模型返回格式无效，本次未生成有效结果。",
  model_not_configured: "AI 问答尚未配置。本地创建案件、导入 PDF、OCR、建立索引和检索仍可使用。",
  model_disabled: "AI 问答调用未启用。请在本地配置中明确启用；仅使用合成或人工脱敏材料。",
  unknown_provider_error: "模型服务发生未知错误，请使用诊断编号排查。",
  index_failed: "索引处理失败，已导入材料仍保留。请重试并查看系统状态。",
  index_processing: "当前案件正在建立索引，请等待完成。",
  local_connection_error: "无法连接本地服务，请确认 CaseFile AI 服务仍在运行。"
};
Object.assign(providerMessages, {
  backup_busy:"数据库持续忙碌，请稍后重试备份。", backup_failed:"完整备份失败，请检查可用空间与目录权限。",
  missing_pdf:"缺少数据库引用的 PDF，已停止操作，无法生成或恢复残缺备份。",
  pdf_checksum:"PDF 与数据库记录的哈希不一致，请核对来源文件。",
  checksum_mismatch:"备份哈希或大小校验失败，请使用完整、未损坏的备份。",
  invalid_manifest:"备份清单或文件列表无效。", invalid_backup:"备份损坏或无法验证。",
  unsafe_archive:"备份包含不安全路径或链接，已拒绝恢复。", unsafe_path:"路径不符合安全限制。",
  backup_too_large:"备份超过当前大小或文件数量限制。",
  backup_format_incompatible:"备份格式不受支持，可能由更高版本创建。",
  application_version_incompatible:"备份应用版本无法验证。",
  future_backup:"此备份由更高版本 CaseFile AI 创建，请使用相应版本恢复。",
  future_schema:"备份数据库结构为未知或更高版本，已拒绝恢复。",
  schema_incompatible:"数据库结构无法验证，已停止操作。",
  database_integrity:"数据库完整性校验未通过，恢复副本无效。",
  database_references:"数据库来源链校验未通过，恢复副本无效。",
  restore_confirmation_required:"请明确确认仅创建新的恢复副本，不覆盖当前工作数据。",
  restore_not_found:"恢复预览不存在，请重新选择备份。",
  restore_already_created:"恢复副本已创建；不会覆盖，请重新上传备份。",
  restore_failed:"恢复未完成，请检查空间与权限；当前工作数据未被覆盖。",
  artifact_not_found:"下载文件不存在，请重新生成。",
  data_management_failed:"数据管理操作未完成，请使用诊断编号排查。"
});
const terms = {validated:"已验证", model_abstained:"证据不足", uncertain:"待确认",
  candidate:"候选事实", contradiction:"存在矛盾", non_contradiction:"未判定为矛盾",
  date:"日期", amount:"金额", action:"行为", person:"人物", exact:"日期明确", partial:"部分日期", unknown:"日期未知"};
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const node = id => document.getElementById(id);
const url = (id, path="") => `/api/cases/${encodeURIComponent(id)}${path}`;
const post = value => ({method:"POST", ...(value === undefined ? {} : {headers:{"Content-Type":"application/json"},body:JSON.stringify(value)})});
const translated = value => terms[value] || "待确认";
const technical = text => `<details class="technical"><summary>技术详情</summary><small>${text}</small></details>`;
let current = null, view = "materials", epoch = 0, creating = false, systemBusy = false;
const pending = new Map(), loads = new Map(), transientResults = new Map();
let caseNames = new Map(), caseRecords = new Map(), globalBusy = false, globalTicket = 0;
let currentConfigurationFile = "Windows 产品模式：%LOCALAPPDATA%\\CaseFileAI\\config.env";
const context = () => ({id:current, epoch});
const valid = ctx => ctx.id === current && ctx.epoch === epoch;

function errorInfo(data, httpStatus) {
  const raw = data?.error || {}, category = Object.hasOwn(providerMessages, raw.category) ? raw.category : null;
  const diagnostic = raw.diagnostic_id || data?.diagnostic_id;
  const diagnosticId = /^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/i.test(diagnostic || "") ? diagnostic : null;
  let message = category ? providerMessages[category] : "处理失败，请稍后重试并查看系统状态。";
  if (!category && httpStatus === 422) message = "输入或文件无法处理，请检查名称、问题或 PDF 是否有效、未加密且大小符合限制。";
  if (!category && httpStatus === 404) message = "案件、材料或记录不存在，请刷新后重试。";
  return {message, category, diagnosticId,
    httpStatus: Number.isInteger(raw.http_status) && raw.http_status >= 100 && raw.http_status <= 599 ? raw.http_status : httpStatus,
    kind: ["model_not_configured","model_disabled"].includes(category) ? "info" : category === "index_processing" ? "warning" : "error"};
}
async function request(path, options={}) {
  let response;
  try { response = await fetch(path, options); }
  catch (_) { throw errorInfo({error:{category:"local_connection_error"}}, null); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw errorInfo(data, response.status);
  return data;
}
function status(text, kind="info", error=null) {
  const target = node("status"); target.dataset.kind = kind; target.textContent = text;
  if (error && (error.category || error.diagnosticId || error.httpStatus)) target.insertAdjacentHTML("beforeend", technical([
    error.category ? `内部代码：${escapeHtml(error.category)}` : "",
    error.diagnosticId ? `诊断编号：${escapeHtml(error.diagnosticId)}` : "",
    error.httpStatus ? `HTTP：${Number(error.httpStatus)}` : ""
  ].filter(Boolean).join(" · ")));
}
function report(error, prefix="处理未完成") { status(`${prefix}：${error.message || "请重试并查看系统状态。"}`, error.kind || "error", error); }
function original(version, label="打开此版本原文") {
  return `<a target="_blank" rel="noopener" href="/api/documents/${encodeURIComponent(version.document_id)}/original?document_version_id=${encodeURIComponent(version.document_version_id)}">${label}</a>`;
}
function evidence(items, versions=new Map()) {
  if (!items?.length) return "<p>无有效引用证据。</p>";
  return `<ol class="citations">${items.map(x=>`<li><a target="_blank" rel="noopener" href="/api/documents/${encodeURIComponent(x.document_id)}/original?document_version_id=${encodeURIComponent(x.document_version_id)}#page=${Number(x.page_number)}">${escapeHtml(x.filename)} · ${escapeHtml(versions.get(x.document_version_id)||"版本信息暂不可用")} · 第 ${Number(x.page_number)} 页</a><blockquote>${escapeHtml(x.quote)}</blockquote>${technical(`Evidence：${escapeHtml(x.evidence_id)} · Page：${escapeHtml(x.page_id)}`)}</li>`).join("")}</ol>`;
}
async function sourceVersions(items) {
  const ids=[...new Set(items.map(x=>x.document_id).filter(Boolean))];
  const documents=await Promise.all(ids.map(id=>request(`/api/documents/${encodeURIComponent(id)}`)));
  const labels=new Map();
  for(const document of documents) for(const version of document.versions) {
    if(Number.isInteger(version.version_number) && version.version_number>0) labels.set(version.document_version_id,`v${version.version_number}${version.document_version_id===document.latest.document_version_id ? "（当前版本）" : "（非当前版本）"}`);
  }
  return labels;
}
function claim(x, cites, versions) { return `<article class="card"><b>${x.kind ? translated(x.kind) : "事实"}</b> ${escapeHtml(x.text)}${technical(`Claim：${escapeHtml(x.claim_id)}`)}${evidence(cites,versions)}</article>`; }
function answerInfo(answer, materials) {
  const reason = answer.metadata?.refusal_reason, validation = answer.metadata?.validation_status;
  if (reason === "index_incomplete") return {kind:"warning", label:"索引未完成", message: materials?.status === "ready" ? "此回答生成时索引尚未完整；当前索引已可用，请重新提问。" : materials?.message || "请到材料页面检查索引状态并建立 / 更新索引。"};
  if (["model_insufficient_evidence","model_abstained","empty_retrieval"].includes(reason) || validation === "model_abstained" || (answer.abstained && !reason && !validation)) return {kind:"info", label:"证据不足", message:"未形成有充分引用支持的答案；可以补充材料或调整问题。"};
  if (answer.abstained || answer.status === "insufficient_evidence") return {kind:"error", label:"答案验证未通过", message:"本次答案未通过引用或事实验证，无法作为有效答案使用。请调整问题后重试。"};
  return {kind:"success", label:"已验证", message:"回答已通过现有验证流程，请结合原文复核。"};
}
function modelNotice(provider) {
  if (!provider.configured) return providerMessages.model_not_configured;
  if (!provider.enabled) return providerMessages.model_disabled;
  return `AI 问答已配置并启用 · ${provider.connection_verified ? "本次运行中曾成功连接" : "连接尚未验证"}。仅使用合成或人工脱敏材料。`;
}
async function refreshSystem() {
  if (systemBusy) return;
  systemBusy = true; const button = node("system-refresh"); button.disabled = true; button.textContent = "正在检查系统状态…";
  try {
    const s = await request("/api/system/status"); node("model-notice").textContent = modelNotice(s.qa_provider);
    if(typeof s.configuration_file==="string")currentConfigurationFile=s.configuration_file;
    node("system-content").innerHTML = `<ul><li>数据库：${s.database.reachable && s.database.schema_status === "current" ? "可用，结构为当前版本" : "不可用或需要迁移，请检查本地配置"}</li><li>数据目录：${s.data_directory.writable ? "可写" : "不可写"}</li><li>本地向量模型：${s.embedding.available ? "可用" : "尚不可用，请检查依赖与模型缓存"} · ${s.embedding.loaded ? "已加载" : "尚未加载"}</li><li>OCR：${s.ocr.dependency_available && s.ocr.model_available ? "依赖与模型可用" : "依赖或模型缺失"}</li><li>AI 问答：${s.qa_provider.configured ? "已配置" : "未配置"} · ${s.qa_provider.enabled ? "已启用" : "未启用"} · ${s.qa_provider.connection_verified ? "曾成功连接" : "连接尚未验证"}</li><li>安全日志：${s.logs.file_logging_available ? "文件日志可用" : "文件日志不可用"}</li></ul><p>刷新只检查本地状态，不发送模型测试请求。</p>`;
    node("system-content").innerHTML+=technical(`数据目录：${escapeHtml(s.data_directory.path||"未提供")}<br>数据库：${escapeHtml(s.database.path||"未提供")}<br>日志目录：${escapeHtml(s.logs.directory||"未提供")}<br>配置文件：${escapeHtml(currentConfigurationFile)}`);
  } catch (error) { node("system-content").textContent = error.message; }
  finally { systemBusy = false; button.disabled = false; button.textContent = "刷新系统状态"; }
}
function syncBusy() {
  const operation = pending.get(current);
  document.querySelectorAll("main .view button, main .view input, main .view select, [data-case-control], #rename-name").forEach(element => {
    element.disabled = Boolean(operation || element.dataset.blocked === "true" || (element.hasAttribute?.("data-case-control") && !current));
    if (element.tagName === "BUTTON") {
      element.dataset.idleLabel ||= element.textContent;
      element.textContent = operation && element.dataset.operation === operation.key ? `${operation.label}中…` : element.dataset.idleLabel;
    }
  });
  document.querySelectorAll("main [data-operation-message]").forEach(element => {
    element.textContent = operation ? `正在为案件“${caseNames.get(current) || "当前案件"}”${operation.label}，请稍候…` : "";
  });
}
async function run(key, label, task, after) {
  if (!current || pending.has(current)) return;
  const ctx = context(), operation = {key, label}; pending.set(ctx.id, operation); syncBusy();
  status(`正在为案件“${caseNames.get(ctx.id) || "当前案件"}”${label}，请稍候…`);
  try {
    const result = await task(ctx.id); if (!valid(ctx)) return;
    status(`${label}完成。`, "success"); if (after) await after(ctx, result);
    if (valid(ctx)) await refreshSystem();
  } catch (error) {
    if (valid(ctx)) {
      report(error, `${label}未完成`);
      if (key === "index") await refresh("materials", ctx).catch(() => {});
    }
  } finally { if (pending.get(ctx.id) === operation) pending.delete(ctx.id); syncBusy(); }
}
function noCase() { for (const name of names) node(`${name}-content`).innerHTML = "<p>请先创建或选择案件，再导入 PDF 开始分析。</p>"; }
function selectCase(id) {
  current = id; epoch++;
  for (const name of names) node(`${name}-content`).replaceChildren();
  updateCaseHeading(); node("export-result").replaceChildren(); syncBusy();
  status(id ? `已选择案件“${caseNames.get(id) || "当前案件"}”。` : "请先创建案件。");
  if (id) loadView(view); else noCase();
}
async function loadCases(preferred=null, expectedEpoch=epoch) {
  const includeArchived=Boolean(node("include-archived").checked);
  const cases = (await request(`/api/cases?include_archived=${includeArchived}`)).cases; if (epoch !== expectedEpoch) return;
  const select = node("case-select"); select.replaceChildren(); caseNames = new Map(cases.map(x => [x.case_id,x.name])); caseRecords = new Map(cases.map(x=>[x.case_id,x]));
  for (const item of cases) { const option=document.createElement("option"); option.value=item.case_id; option.textContent=item.name + (item.status==="archived" ? "（已归档）" : ""); select.append(option); }
  const id=cases.some(x=>x.case_id===preferred) ? preferred : cases[0]?.case_id || null;
  select.value=id || ""; selectCase(id);
}
function show(name) {
  view = name;
  for (const item of names) { node(`view-${item}`).hidden = item !== name; document.querySelector(`[data-view="${item}"]`).classList.toggle("active",item===name); }
  if (current) loadView(name); else noCase();
}
function loadView(name) { const ctx=context(); refresh(name,ctx).catch(error=>{if(valid(ctx)) report(error,"载入未完成");}); }
function uploadForm(documentId="") {
  const suffix = documentId || "new";
  return `<form data-form="upload" class="row"><label for="file-${suffix}">${documentId ? "选择新版本 PDF" : "选择 PDF 材料"}</label><input id="file-${suffix}" name="file" type="file" accept=".pdf,application/pdf" required>${documentId ? `<input type="hidden" name="document_id" value="${escapeHtml(documentId)}">` : ""}<button data-operation="upload">${documentId ? "添加新版本" : "导入 PDF"}</button><span data-operation-message role="status"></span></form>`;
}
function materialCard(item) {
  const x = item.version;
  return `<article class="card"><h3>${escapeHtml(x.filename)}</h3><p>${Number(x.page_count)} 页 · 当前版本 v${Number(x.version_number)}</p><p class="notice" data-kind="${item.status === "failed" ? "error" : item.status === "ready" ? "success" : "warning"}"><b>${escapeHtml(item.label)}</b> · ${escapeHtml(item.message)}</p>${original(x)}<details><summary>历史版本 / 添加新版本</summary><div id="versions-${escapeHtml(x.document_id)}"><button data-action="versions" data-document="${escapeHtml(x.document_id)}" data-operation="versions">查看历史版本</button></div>${uploadForm(x.document_id)}</details>${technical(`Document：${escapeHtml(x.document_id)} · Version：${escapeHtml(x.document_version_id)}<br>SHA-256：${escapeHtml(x.content_sha256)}<br>解析版本：${escapeHtml(x.parser_version)}${item.diagnostic_id ? `<br>诊断编号：${escapeHtml(item.diagnostic_id)}` : ""}`)}</article>`;
}
function versionHistory(versions) {
  return `<ul>${versions.map((x,i)=>`<li>v${Number(x.version_number)}${i===0 ? "（当前）" : ""} · ${Number(x.page_count)} 页 · ${escapeHtml(x.filename)} · ${original(x)}</li>`).join("")}</ul><p>历史版本保留原始引用；未记录材料创建时间。</p>`;
}
const isTransient = r => ["model_unavailable","model_output_invalid"].includes(r.basis);
function mergeResults(saved, temporary) {
  const ids=new Set(saved.map(r=>r.result_id));
  return [...saved.map(r=>({...r, temporary:false})), ...(temporary || []).filter(r=>isTransient(r) && !ids.has(r.result_id)).map(r=>({...r,temporary:true}))];
}
function contradictionCard(r, byId, versions) {
  const kind=r.temporary || r.verdict === "uncertain" ? "warning" : r.verdict === "contradiction" ? "error" : "success";
  const reason=r.temporary ? "本次语义检测未完成，结果待确认。可稍后重试。此临时反馈未持久化，重新加载页面后不会保留。" : r.reason;
  return `<article class="card notice" data-kind="${kind}"><h3>${translated(r.contradiction_type)} · ${translated(r.verdict)}${r.temporary ? "（本次临时反馈）" : ""}</h3><p>${escapeHtml(reason)}</p>${technical(`内部代码：${escapeHtml(r.basis)} · Result：${escapeHtml(r.result_id)}`)}${claim(byId.get(r.left_claim_id)||{text:"候选事实 A"},r.left_evidence,versions)}${claim(byId.get(r.right_claim_id)||{text:"候选事实 B"},r.right_evidence,versions)}</article>`;
}
async function refresh(name, ctx=context()) {
  if (!valid(ctx)) return;
  const ticket=(loads.get(name)||0)+1; loads.set(name,ticket);
  const id=ctx.id, target=node(`${name}-content`); let html="";
  if (name === "materials") {
    const materials=await request(url(id,"/materials"));
    html=`<p>导入 PDF 时已完成文本解析 / OCR。建立或更新索引只处理已有文本，不会重新执行 OCR。</p>${uploadForm()}<div class="operations"><button data-action="index" data-operation="index" data-blocked="${!materials.documents.length || materials.status === "processing"}">${escapeHtml(materials.action_label)}</button><span data-operation-message role="status"></span></div><p class="notice" data-kind="${materials.status === "failed" ? "error" : materials.status === "ready" ? "success" : "warning"}">${escapeHtml(materials.label)} · ${escapeHtml(materials.message)}</p><h2>材料</h2>${materials.documents.map(materialCard).join("") || "<p>尚无材料。请导入 PDF；索引建立后即可检索。</p>"}<h2>检索</h2><form data-form="search" class="row"><label for="search-query">检索案件材料</label><input id="search-query" name="query" required maxlength="1000"><button data-operation="search" data-blocked="${materials.status !== "ready"}">检索</button><span data-operation-message role="status"></span></form><div id="search-results"></div>`;
  } else if (name === "qa") {
    const [answers,materials]=await Promise.all([request(url(id,"/answers")),request(url(id,"/materials"))]);
    const versions=await sourceVersions(answers.flatMap(a=>a.citations));
    html=`${!materials.documents.length ? "<p>本案尚无材料，请先导入 PDF 并建立索引。</p>" : `<p>${escapeHtml(materials.message)}</p>`}<form data-form="ask" class="row"><label for="question">询问材料中的事实</label><input id="question" name="question" required maxlength="1000"><button data-operation="ask" data-blocked="${!materials.documents.length}">提问</button><span data-operation-message role="status"></span></form><h2>回答历史</h2>${answers.map(a=>{
      const info=answerInfo(a,materials);
      return `<article class="card"><h3>${escapeHtml(a.question)}</h3><p class="notice" data-kind="${info.kind}"><b>${info.label}</b> · ${escapeHtml(info.message)}</p><p>${escapeHtml(a.answer)}</p>${a.claims.map(c=>claim(c,a.citations.filter(e=>c.citation_ids.includes(e.evidence_id)),versions)).join("")}${technical(`Answer：${escapeHtml(a.answer_id)} · ${Math.round(a.metadata?.total_ms||0)} ms`)}</article>`;
    }).join("")||"<p>暂无回答。导入材料并建立索引后，可以提出第一个问题；AI 问答需要配置并明确启用。</p>"}`;
  } else if (name === "claims") {
    const [answers,claims]=await Promise.all([request(url(id,"/answers")),request(url(id,"/claims"))]);
    const versions=await sourceVersions(answers.flatMap(a=>a.citations));
    const byAnswer=new Map(answers.map(a=>[a.answer_id,a]));
    html=`<h2>回答与覆盖检查</h2>${answers.filter(a=>!a.abstained).map(a=>`<article class="card"><b>${escapeHtml(a.question)}</b><div class="operations"><button data-action="extract" data-answer="${escapeHtml(a.answer_id)}" data-operation="extract">抽取事实</button><button data-action="coverage" data-answer="${escapeHtml(a.answer_id)}" data-operation="coverage">查看覆盖检查</button><span data-operation-message role="status"></span></div><div id="coverage-${escapeHtml(a.answer_id)}"></div></article>`).join("")||"<p>尚无可用于抽取事实的有效回答。请先获得有引用支持的回答。</p>"}<h2>已抽取事实</h2>${claims.map(c=>claim(c,byAnswer.get(c.answer_id)?.citations.filter(e=>c.evidence_ids.includes(e.evidence_id))||[],versions)).join("")||"<p>暂无事实。已有有效回答时，点击“抽取事实”。</p>"}`;
  } else if (name === "conflicts") {
    const [candidates,saved,claims]=await Promise.all([request(url(id,"/conflict-candidates")),request(url(id,"/contradictions")),request(url(id,"/claims"))]);
    const versions=await sourceVersions([...candidates,...saved,...(transientResults.get(id)||[])].flatMap(c=>[...c.left_evidence,...c.right_evidence]));
    const results=mergeResults(saved,transientResults.get(id)), byId=new Map(claims.map(c=>[c.claim_id,c]));
    html=`<div class="operations"><button data-action="detect" data-operation="detect" data-blocked="${!candidates.length}">运行四类矛盾检测</button><span data-operation-message role="status"></span></div><h2>候选事实对 (${candidates.length})</h2>${candidates.map(c=>`<article class="card"><b>候选事实</b> · ${escapeHtml(c.shared_topic)}${technical(`Candidate：${escapeHtml(c.candidate_id)}`)}${claim(c.left_claim,c.left_evidence,versions)}${claim(c.right_claim,c.right_evidence,versions)}</article>`).join("")||"<p>暂无可比较的候选事实；请先抽取事实并检查材料覆盖。没有候选或结果不代表案件不存在矛盾。</p>"}<h2>检测结果 (${results.length})</h2>${results.map(r=>contradictionCard(r,byId,versions)).join("")||"<p>尚无检测结果。没有结果不等于不存在矛盾；可先运行检测。</p>"}`;
  } else {
    const events=await request(url(id,"/timeline"));
    const versions=await sourceVersions(events.flatMap(e=>e.evidence));
    html=`<p>未知日期不会推断。</p>${events.map(e=>`<article class="card"><h3>${escapeHtml(e.date_value||"未知日期")} <small>${translated(e.date_status)}</small></h3><p>${escapeHtml(e.fact)}</p>${technical(`Claim：${escapeHtml(e.claim_id)} · 关联结果：${escapeHtml(e.contradiction_result_ids.join(", ")||"无")}`)}${evidence(e.evidence,versions)}</article>`).join("")||"<p>暂无可追溯的时间线事件。请先获得有效回答并抽取事实。</p>"}`;
  }
  if (valid(ctx) && loads.get(name)===ticket) { target.innerHTML=html; syncBusy(); }
}
async function submitCase(event) {
  event.preventDefault(); if (creating) return;
  creating=true; const startEpoch=epoch, form=node("case-form");
  form.querySelectorAll("button,input").forEach(x=>{x.disabled=true;});
  form.querySelector("[data-operation-message]").textContent="正在创建案件…";
  try {
    const item=await request("/api/cases",post({name:node("case-name").value}));
    if (epoch===startEpoch) {
      await loadCases(item.case_id,startEpoch);
      if (current===item.case_id && epoch===startEpoch+1) { form.reset(); status("创建案件完成。","success"); }
    }
  } catch(error) { if(epoch===startEpoch) report(error,"创建案件未完成"); }
  finally { creating=false; form.querySelectorAll("button,input").forEach(x=>{x.disabled=false;}); form.querySelector("[data-operation-message]").textContent=""; }
}
function submitMain(event) {
  event.preventDefault(); const form=event.target;
  if (form.dataset.form === "upload") {
    const body=new FormData(form);
    run("upload","导入 PDF 并解析 / OCR",id=>request(url(id,"/documents"),{method:"POST",body}),ctx=>refresh("materials",ctx));
  }
  if (form.dataset.form === "ask") {
    const question=form.elements.question.value;
    run("ask","生成并验证回答",id=>request(url(id,"/ask"),post({question})),async(ctx,result)=>{
      await refresh("qa",ctx); if(!valid(ctx)) return;
      const materials=await request(url(ctx.id,"/materials")); if(!valid(ctx)) return;
      const info=answerInfo(result,materials); status(`${info.label}：${info.message}`,info.kind);
    });
  }
  if (form.dataset.form === "search") {
    const query=form.elements.query.value;
    run("search","检索",id=>request(url(id,`/search?q=${encodeURIComponent(query)}`)),(ctx,hits)=>{
      if(valid(ctx) && node("search-results")) node("search-results").innerHTML=hits.map(h=>`<article class="card"><b>${escapeHtml(h.filename)} · 第 ${Number(h.page_number)} 页</b><blockquote>${escapeHtml(h.text)}</blockquote>${original(h)}</article>`).join("")||"<p>未找到匹配材料。可调整关键词后重试。</p>";
    });
  }
}
function clickMain(event) {
  const b=event.target.closest("button[data-action]"); if(!b || b.disabled) return;
  const answer=b.dataset.answer, documentId=b.dataset.document;
  if(b.dataset.action.startsWith("export-")) {
    const format=b.dataset.action==="export-json" ? "json" : "markdown";
    run(b.dataset.action,"导出案件分析",id=>request(url(id,`/exports/${format}`),post()),(ctx,result)=>{
      if(valid(ctx)) node("export-result").innerHTML=downloadLink(result,"下载分析报告") + "<p>导出包含已有分析及引用片段；不是可恢复的完整备份。</p>";
    });
  }
  if(b.dataset.action === "index") run("index","建立 / 更新索引",id=>request(url(id,"/index"),post()),ctx=>refresh("materials",ctx));
  if(b.dataset.action === "extract") run("extract","抽取事实",id=>request(url(id,`/answers/${encodeURIComponent(answer)}/claims`),post()),ctx=>refresh("claims",ctx));
  if(b.dataset.action === "versions") run("versions","读取历史版本",()=>request(`/api/documents/${encodeURIComponent(documentId)}`),(ctx,result)=>{
    const target=node(`versions-${documentId}`); if(valid(ctx) && target) target.innerHTML=versionHistory(result.versions);
  });
  if(b.dataset.action === "coverage") run("coverage","检查覆盖情况",id=>request(url(id,`/answers/${encodeURIComponent(answer)}/coverage`)),async(ctx,r)=>{
    const versions=await sourceVersions(r.claims.flatMap(c=>c.evidence)); const target=node(`coverage-${answer}`); if(valid(ctx) && target) target.innerHTML=`<p class="notice" data-kind="${r.complete ? "success" : "warning"}">完整覆盖：${r.complete?"是":"否"}</p>${r.sources.map(s=>`<p>${escapeHtml(s.source_text)}<br>未覆盖：${escapeHtml(s.uncovered_fragments.map(f=>f.text).join("；")||"无")}</p>`).join("")}${r.claims.map(c=>`<p>引用证据${c.valid_evidence?"有效":"无效，需复核"}</p>${evidence(c.evidence,versions)}`).join("")}`;
  });
  if(b.dataset.action === "detect") run("detect","检测矛盾",id=>request(url(id,"/contradictions"),post()),async(ctx,results)=>{
    if(!valid(ctx)) return; transientResults.set(ctx.id,results.filter(isTransient));
    await refresh("conflicts",ctx);
    if(valid(ctx) && results.some(isTransient)) status("本次部分语义检测未完成，结果待确认。临时反馈已展示且未持久化，可稍后重试。","warning");
  });
}
function bootstrap() {
  document.querySelectorAll("nav button").forEach(b=>b.addEventListener("click",()=>show(b.dataset.view)));
  node("case-select").addEventListener("change",event=>selectCase(event.target.value||null));
  node("case-form").addEventListener("submit",submitCase);
  document.querySelector("main").addEventListener("submit",submitMain);
  document.querySelector("main").addEventListener("click",clickMain);
  node("system-refresh").addEventListener("click",refreshSystem);
  node("include-archived").addEventListener("change",()=>{const selected=current; epoch++;const expected=epoch;loadCases(selected,expected).catch(error=>{if(epoch===expected)report(error,"载入案件未完成");});});
  node("rename-form").addEventListener("submit",event=>{
    event.preventDefault();renameCurrentCase(node("rename-name").value);
  });
  node("archive-case").addEventListener("click",archiveCurrentCase);
  node("backup-create").addEventListener("click",()=>runGlobal("创建完整备份",()=>request("/api/data/backups",post()),result=>{node("data-result").innerHTML=downloadLink(result,"下载完整备份");}));
  node("restore-form").addEventListener("submit",event=>{
    event.preventDefault();if(globalBusy)return;const body=new FormData(event.target);node("restore-preview").replaceChildren();
    runGlobal("验证恢复包",()=>request("/api/data/restores/preview",{method:"POST",body}),result=>{node("restore-preview").innerHTML=restorePreviewHtml(result);});
  });
  node("data-panel").addEventListener("click",event=>{
    const button=event.target.closest("button[data-restore-id]");if(!button||button.disabled)return;
    confirmRestoreButton(button);
  });
  show("materials"); loadCases().catch(error=>report(error,"载入未完成")); refreshSystem();
}
// Dependency-free tests execute the same functions used by the browser.
if (typeof module !== "undefined" && module.exports) module.exports = {
  errorInfo, answerInfo, modelNotice, mergeResults, materialCard, versionHistory, contradictionCard, evidence, sourceVersions, refreshSystem,
  run, refresh, syncBusy, submitCase, selectCase, valid, context,
  setState: state=>{current=state.current; epoch=state.epoch; caseNames=new Map(state.names || [[current,"合成测试案件"]]);caseRecords=new Map(state.records || []);},
  pending, loads
};
else bootstrap();

function updateCaseHeading() {
  const record=caseRecords.get(current), name=caseNames.get(current);
  node("case-heading").textContent=current?`${name || "当前案件"}${record?.status==="archived"?"（已归档，仍可分析）":""}`:"尚未选择案件";
  node("rename-name").value=name || "";
  const archived=record?.status==="archived", button=node("archive-case");button.textContent=archived?"恢复归档":"归档案件";button.dataset.idleLabel=button.textContent;
  document.title=current?`${name || "当前案件"} · CaseFile AI`:"CaseFile AI";
}
function renameCurrentCase(name) {
  return run("rename","重命名案件",id=>request(url(id,"/name"),{...post({name}),method:"PATCH"}),async(ctx,result)=>{
    if(!valid(ctx))return;caseNames.set(ctx.id,result.name);caseRecords.set(ctx.id,result);updateCaseHeading();
    const select=node("case-select");for(const option of select.options)if(option.value===ctx.id)option.textContent=result.name+(result.status==="archived"?"（已归档）":"");
  });
}
function archiveCurrentCase() {
  const archived=caseRecords.get(current)?.status!=="archived";
  return run("archive",archived?"归档案件":"恢复归档",id=>request(url(id,"/archive"),{...post({archived}),method:"PATCH"}),async(ctx)=>{
    if(valid(ctx))await loadCases(ctx.id,ctx.epoch);
  });
}
function downloadLink(result,label) {
  if(!/^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/.test(result.artifact_id))return "<p>下载标识无效，请重新生成。</p>";
  return `<a href="/api/data/artifacts/${result.artifact_id}" download>${escapeHtml(label)} · ${escapeHtml(result.filename)}</a>`;
}
function restorePreviewHtml(result) {
  return `<article class="card"><h3>已验证的恢复预览</h3><p>${escapeHtml(result.message)}</p><p>案件 ${Number(result.counts.cases)} · 材料 ${Number(result.counts.documents)} · 历史版本 ${Number(result.counts.document_versions)}</p><label class="check-label"><input id="restore-understood" type="checkbox">我理解：此操作仅创建新的恢复副本，不覆盖、不删除、不自动切换当前工作数据。</label><button type="button" data-restore-id="${escapeHtml(result.restore_id)}">确认创建新副本</button></article>`;
}
function restoreResultHtml(result) {
  const settings=`CASEFILE_DATA_DIR=${result.data_directory}\nCASEFILE_DATABASE_URL=sqlite:///${result.database_path.replaceAll("\\","/")}`;
  return `<article class="card"><h3>恢复副本已验证完成</h3><p>当前工作数据保持原样。案件 ${Number(result.counts.cases)} · 材料 ${Number(result.counts.documents)} · 历史版本 ${Number(result.counts.document_versions)}</p><p>切换步骤：停止当前服务 → 将当前配置文件的以下两项改为恢复副本路径 → 重新启动服务 → 用系统状态核对数据库和材料。保留原路径，需回退时改回。</p><p>配置文件：${escapeHtml(currentConfigurationFile)}。Windows 产品入口默认使用 config.env；若设置了其他配置入口，请以系统状态技术详情为准。</p><pre>${escapeHtml(settings)}</pre><p>API key 和模型配置不会从备份恢复；继续使用独立的本地配置。</p></article>`;
}
async function runGlobal(label,task,after) {
  if(globalBusy)return;globalBusy=true;const ticket=++globalTicket;
  document.querySelectorAll("#data-panel button, #data-panel input").forEach(element=>{element.disabled=true;});node("data-operation").textContent=`正在${label}，请稍候…`;
  try {const result=await task();if(ticket===globalTicket){after(result);node("data-operation").textContent=`${label}完成。`;}}
  catch(error){if(ticket===globalTicket){node("data-operation").textContent=`${label}未完成：${error.message||"请检查安全日志。"}`;report(error,`${label}未完成`);}}
  finally {globalBusy=false;document.querySelectorAll("#data-panel button, #data-panel input").forEach(element=>{element.disabled=false;});}
}
function confirmRestoreButton(button) {
  const confirmation=node("restore-understood");if(!confirmation?.checked){node("data-operation").textContent="请先确认理解：仅创建新副本，不覆盖当前数据。";return;}
  return runGlobal("创建新的恢复副本",()=>request(`/api/data/restores/${encodeURIComponent(button.dataset.restoreId)}/confirm`,post({confirmation:"create_new_copy"})),result=>{
    node("restore-preview").replaceChildren();node("data-result").innerHTML=restoreResultHtml(result);
  });
}
if(typeof module!=="undefined"&&module.exports)Object.assign(module.exports,{downloadLink,restorePreviewHtml,restoreResultHtml,runGlobal,updateCaseHeading,renameCurrentCase,archiveCurrentCase,loadCases,confirmRestoreButton});
