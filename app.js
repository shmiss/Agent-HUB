const $ = (id) => document.getElementById(id);
const storeKey = 'agent-hub-v1';
const textExt = /\.(md|txt|py|js|ts|tsx|jsx|json|yaml|yml|html|css|csv|log)$/i;
let settings = {
  baseUrl: 'http://127.0.0.1:8642/v1',
  model: 'hermes-agent',
  apiKey: '',
  agentProfiles: [],
  discoveredAgents: [],
  activeAgentId: null,
  configPath: ''
};
let chats = [];
let activeId = null;
let attachments = [];
let busy = false;
let runTicker = null;
let activeProgressId = null;
let editingProfileId = null;
const isFileMode = window.location.protocol === 'file:';
const localProxyUrl = 'http://127.0.0.1:8765';
const MAX_CONTEXT_MESSAGES = 8;
const MAX_CONTEXT_CHARS = 6000;
const MAX_SINGLE_MESSAGE_CHARS = 3000;
const runPhases = [
  { delay: 0, text: '请求已发出，正在连接 Agent' },
  { delay: 900, text: 'Agent 已接收任务，正在整理上下文' },
  { delay: 2200, text: '正在等待上游模型响应' },
  { delay: 5200, text: '任务耗时较长，Agent 仍在执行中' }
];
function isOpenClawAdapter(profile){
  return profile?.adapter === 'openclaw-cli' || profile?.adapter === 'openclaw-gateway-rpc';
}
function runPhasesForProfile(profile, sessionId){
  if(!isOpenClawAdapter(profile)) return runPhases;
  if(profile?.adapter === 'openclaw-gateway-rpc') {
    return [
      { delay: 0, text: '正在连接 OpenClaw Gateway WebSocket' },
      { delay: 500, text: `已绑定 OpenClaw session：${sessionId}` },
      { delay: 1200, text: `正在通过 Gateway RPC 调用 agent:${profile.agentId || 'main'}` },
      { delay: 4200, text: 'OpenClaw 正在执行任务，等待最终回复' }
    ];
  }
  return [
    { delay: 0, text: '正在启动 OpenClaw CLI bridge' },
    { delay: 700, text: `已绑定 OpenClaw session：${sessionId}` },
    { delay: 1500, text: `正在调用 openclaw agent --agent ${profile.agentId || 'main'} --json` },
    { delay: 4800, text: 'OpenClaw 正在执行任务，等待最终回复' }
  ];
}

function uid(){ return 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2,8); }
function profileId(){ return 'agent_' + Date.now().toString(36) + Math.random().toString(36).slice(2,7); }
function nowTitle(){ return new Date().toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }
function escapeHtml(s){ return String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function stripPersistedContent(content){
  if(!Array.isArray(content)) return content;
  return content.map(part => {
    if(part?.type !== 'image_url') return part;
    return {...part, image_url: {url: '[image omitted from localStorage]'}};
  });
}
function chatsForStorage(){
  return (chats || []).slice(0, 40).map(chat => ({
    ...chat,
    messages: (chat.messages || []).slice(-80).map(message => ({
      ...message,
      content: stripPersistedContent(message.content)
    }))
  }));
}
function settingsForStorage(){
  return {
    ...settings,
    agentProfiles: (settings.agentProfiles || []).map(profile => ({...profile, apiKey: profile.apiKey ? '' : ''})),
    discoveredAgents: (settings.discoveredAgents || []).map(profile => ({...profile, apiKey: ''}))
  };
}
function save(){
  try{
    localStorage.setItem(storeKey, JSON.stringify({settings: settingsForStorage(), chats: chatsForStorage(), activeId}));
  }catch(e){
    console.warn('Agent Hub localStorage save skipped:', e.message);
  }
}
function agentProfileKey(profile){
  const p = profile || {};
  const adapter = String(p.adapter || '').trim().toLowerCase();
  const type = String(p.type || '').trim().toLowerCase();
  const base = String(p.baseUrl || p.binaryPath || '').trim().replace(/\/$/, '');
  const agentId = String(p.agentId || '').trim();
  const model = String(p.model || '').trim();
  return [type, adapter, base, agentId, model].join('|');
}
function uniqueProfiles(list, activeId = null){
  const result = [];
  const seen = new Map();
  const idMap = {};
  (list || []).map(normalizeProfile).filter(Boolean).forEach(profile => {
    const key = agentProfileKey(profile);
    if(!seen.has(key)){
      seen.set(key, result.length);
      result.push(profile);
      idMap[profile.id] = profile.id;
      return;
    }
    const index = seen.get(key);
    const kept = result[index];
    if(activeId && profile.id === activeId){
      idMap[kept.id] = profile.id;
      idMap[profile.id] = profile.id;
      result[index] = profile;
    } else {
      idMap[profile.id] = kept.id;
      ['notes','configSource','binaryPath','status'].forEach(field => {
        if(!kept[field] && profile[field]) kept[field] = profile[field];
      });
    }
  });
  return {profiles: result, idMap};
}
function normalizeProfile(profile){
  if(!profile) return null;
  const adapter = profile.adapter || (profile.type === 'openclaw' ? 'openclaw-gateway-rpc' : 'openai-chat');
  let baseUrl = String(profile.baseUrl || '').trim();
  if(adapter === 'openai-chat') baseUrl = baseUrl.replace(/\/$/, '');
  return {
    id: profile.id || profileId(),
    label: profile.label || '未命名 Agent',
    type: profile.type || 'custom',
    adapter,
    baseUrl,
    model: profile.model || '',
    apiKey: profile.apiKey || '',
    agentId: profile.agentId || '',
    binaryPath: profile.binaryPath || '',
    supportsStream: profile.supportsStream !== false,
    supportsVision: !!profile.supportsVision,
    source: profile.source || 'manual',
    notes: profile.notes || '',
    reachable: profile.reachable,
    status: profile.status || '',
    configSource: profile.configSource || '',
    binaryPath: profile.binaryPath || '',
    apiServerEnabled: profile.apiServerEnabled,
    gatewayPort: profile.gatewayPort,
    gatewayAuthMode: profile.gatewayAuthMode || ''
  };
}
function ensureHubDefaults(){
  const dedupedProfiles = uniqueProfiles(settings.agentProfiles || [], settings.activeAgentId);
  settings.agentProfiles = dedupedProfiles.profiles;
  if(settings.activeAgentId && dedupedProfiles.idMap[settings.activeAgentId]) settings.activeAgentId = dedupedProfiles.idMap[settings.activeAgentId];
  settings.discoveredAgents = uniqueProfiles(settings.discoveredAgents || []).profiles;
  if(!settings.agentProfiles.length){
    const fallback = normalizeProfile({
      id: 'local-hermes-default',
      label: 'Hermes 默认',
      type: 'hermes',
      adapter: 'openai-chat',
      baseUrl: settings.baseUrl || 'http://127.0.0.1:8642/v1',
      model: settings.model || 'hermes-agent',
      apiKey: settings.apiKey || '',
      supportsStream: true,
      supportsVision: true,
      source: 'local-cache',
      notes: '从浏览器本地缓存恢复。'
    });
    settings.agentProfiles = [fallback];
    settings.activeAgentId = fallback.id;
  }
  if(!settings.activeAgentId || !settings.agentProfiles.find(p=>p.id===settings.activeAgentId)){
    settings.activeAgentId = settings.agentProfiles[0].id;
  }
}
function load(){
  try{
    const raw = JSON.parse(localStorage.getItem(storeKey) || '{}');
    settings = {...settings, ...(raw.settings||{})};
    chats = raw.chats || [];
    activeId = raw.activeId || null;
  }catch{}
  ensureHubDefaults();
  syncConnectionFromActiveProfile();
  if(!chats.length) createChat(false);
  if(!activeId || !chats.find(c=>c.id===activeId)) activeId = chats[0].id;
}
function activeChat(){ return chats.find(c=>c.id===activeId); }
function getProfile(id){ return settings.agentProfiles.find(p => p.id === id); }
function getActiveProfile(){ ensureHubDefaults(); return getProfile(settings.activeAgentId) || settings.agentProfiles[0]; }
function activeAgentName(){ return getActiveProfile()?.label || 'Agent'; }
function activeModelLabel(){ const p = getActiveProfile(); return p?.model || settings.model || 'agent'; }
function systemMessage(){ return {role:'system', content:`你是 ${activeAgentName()} 企业智能体。请用中文、结构化、专业但简洁的方式回答。用户可能上传图片或文本附件，请结合附件内容。`}; }
function createChat(render=true){
  const c = {id:uid(), title:'新的会话 ' + nowTitle(), createdAt:Date.now(), messages:[systemMessage()]};
  chats.unshift(c); activeId = c.id; save(); if(render) renderAll(); return c;
}
function syncConnectionFromActiveProfile(){
  ensureHubDefaults();
  const profile = getActiveProfile();
  if(!profile) return;
  settings.activeAgentId = profile.id;
  settings.baseUrl = profile.baseUrl || settings.baseUrl;
  settings.model = profile.model || settings.model;
  settings.apiKey = profile.apiKey || '';
}
function syncSystemPrompts(){
  const name = activeAgentName();
  chats.forEach(chat => {
    const first = chat.messages?.[0];
    if(first?.role === 'system'){
      first.content = `你是 ${name} 企业智能体。请用中文、结构化、专业但简洁的方式回答。用户可能上传图片或文本附件，请结合附件内容。`;
    }
  });
}
function updateWorkspaceChrome(){
  const profile = getActiveProfile();
  $('composerMode').textContent = profile ? `${profile.label}${profile.adapter === 'openclaw-gateway-rpc' ? ' · Gateway RPC' : profile.adapter === 'openclaw-gateway' ? ' · Gateway' : profile.adapter === 'openclaw-cli' ? ' · CLI' : ''}` : '未配置 Agent';
  $('subTitle').textContent = profile
    ? `${profile.label} · ${profile.adapter === 'openai-chat' ? 'OpenAI-compatible' : profile.adapter === 'openclaw-gateway-rpc' ? 'OpenClaw Gateway RPC' : profile.adapter === 'openclaw-cli' ? 'OpenClaw CLI' : 'OpenClaw Gateway'} · 多模态入口 · 浏览器经本地服务代理访问`
    : 'Agent Hub · 多模态入口 · 浏览器经本地服务代理访问';
}
function renderAll(){ syncConnectionFromActiveProfile(); updateWorkspaceChrome(); renderSettings(); renderChatList(); renderMessages(); }
function renderChatList(){
  const q = $('searchChat').value.trim().toLowerCase();
  $('chatList').innerHTML = '';
  chats.filter(c=>!q || c.title.toLowerCase().includes(q)).forEach(c=>{
    const b = document.createElement('button'); b.className = 'chat-item' + (c.id===activeId?' active':''); b.textContent = c.title;
    b.onclick = () => { activeId=c.id; save(); renderAll(); };
    $('chatList').appendChild(b);
  });
}
function roleLabel(role){ return role==='user'?'你':role==='assistant'?'A':role==='error'?'!':'S'; }
function displayContent(content){
  if(Array.isArray(content)) return content.map(p => p.type==='text' ? p.text : '[图片附件]').join('\n');
  return String(content ?? '');
}
function formatElapsed(ms){ return (ms / 1000).toFixed(1) + 's'; }
function activeVisibleMessages(chat){ return (chat?.messages || []).filter(m=>m.role!=='system'); }
function contentToText(content){
  if(Array.isArray(content)) {
    return content.map(part => {
      if(part.type === 'text') return part.text || '';
      if(part.type === 'image_url') return '[image]';
      return JSON.stringify(part);
    }).join('\n');
  }
  return String(content ?? '');
}
function trimMessageContent(content, maxChars = MAX_SINGLE_MESSAGE_CHARS){
  if(Array.isArray(content)) {
    return content.map(part => {
      if(part.type !== 'text' || !part.text) return part;
      if(part.text.length <= maxChars) return part;
      return {...part, text: part.text.slice(0, maxChars) + '\n\n[内容过长，已截断]'};
    });
  }
  const text = String(content ?? '');
  return text.length <= maxChars ? text : text.slice(0, maxChars) + '\n\n[内容过长，已截断]';
}
function messagesForApi(chat){
  const all = (chat?.messages || []).filter(m => m.role === 'system' || m.role === 'user' || m.role === 'assistant');
  const system = all.find(m => m.role === 'system');
  const conversational = all.filter(m => m.role !== 'system');
  const selected = [];
  let totalChars = system ? contentToText(system.content).length : 0;

  for(let i = conversational.length - 1; i >= 0; i -= 1){
    const msg = conversational[i];
    const textLength = contentToText(msg.content).length;
    if(selected.length >= MAX_CONTEXT_MESSAGES) break;
    if(selected.length > 0 && totalChars + textLength > MAX_CONTEXT_CHARS) break;
    selected.unshift({ role: msg.role, content: trimMessageContent(msg.content) });
    totalChars += textLength;
  }

  const payload = [];
  if(system) payload.push({ role: system.role, content: trimMessageContent(system.content) });
  return payload.concat(selected);
}
function summarizeApiContext(messages){
  const conversational = messages.filter(m => m.role !== 'system');
  const chars = messages.reduce((sum, msg) => sum + contentToText(msg.content).length, 0);
  return `${conversational.length} 条上下文 · ~${chars} chars`;
}
function renderMessages(){
  const c = activeChat();
  $('chatTitle').textContent = c?.title || '新的企业智能体会话';
  const visible = activeVisibleMessages(c);
  $('messages').innerHTML = '';
  $('emptyState').style.display = visible.length ? 'none' : 'block';
  $('messages').style.display = visible.length ? 'block' : 'none';
  for(const m of visible){
    const row = document.createElement('article'); row.className = 'message ' + (m.role || 'assistant');
    if(m.role === 'progress'){
      const steps = (m.steps || []).map(step => {
        const state = step.state || 'pending';
        return `<div class="progress-step ${state}"><div class="progress-dot"></div><div>${escapeHtml(step.text)}</div></div>`;
      }).join('');
      row.innerHTML = `<div class="avatar">${roleLabel(m.role)}</div><div class="progress-card"><div class="progress-title">${escapeHtml(m.title || `${activeAgentName()} 正在处理`)}</div><div class="progress-steps">${steps}</div><div class="progress-note">${escapeHtml(m.meta || '')}</div></div>`;
    } else {
      row.innerHTML = `<div class="avatar">${roleLabel(m.role)}</div><div><div class="content">${escapeHtml(displayContent(m.content))}</div>${m.meta?`<div class="meta">${escapeHtml(m.meta)}</div>`:''}</div>`;
    }
    $('messages').appendChild(row);
  }
  $('messages').scrollTop = $('messages').scrollHeight;
}
function setBadge(type, text){ const b=$('connBadge'); b.className='badge '+type; b.textContent=text; }
function setBusy(v){ busy=v; $('sendBtn').disabled=v; $('sendBtn').textContent=v?'处理中…':'发送'; }
function showBootNotice(show){ $('bootNotice').classList.toggle('hidden', !show); }
function updateRunStatus(progress){
  const wrap = $('runStatus');
  if(!progress){ wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  $('runStatusTitle').textContent = progress.title || `${activeAgentName()} 正在处理`;
  $('runStatusText').textContent = progress.statusText || '请求已发出，正在等待 Agent 返回。';
  $('runStatusElapsed').textContent = formatElapsed(Date.now() - progress.startedAt);
}
function stopRunTicker(){ if(runTicker){ clearInterval(runTicker); runTicker = null; } }
function startRunTicker(chatId, progressId){
  stopRunTicker();
  runTicker = setInterval(() => {
    const chat = chats.find(c => c.id === chatId);
    const progress = chat?.messages.find(m => m.id === progressId);
    if(!progress){ stopRunTicker(); updateRunStatus(null); return; }
    progress.meta = `${formatElapsed(Date.now() - progress.startedAt)} · ${progress.statusText}`;
    updateRunStatus(progress);
    renderMessages();
  }, 250);
}
function markStep(progress, index, state){
  progress.steps.forEach((step, i) => {
    if(i < index) step.state = 'done';
    if(i === index) step.state = state;
  });
}
function createProgressMessage(chat, attachmentCount){
  const profile = getActiveProfile();
  const isOpenClaw = isOpenClawAdapter(profile);
  const isRpc = profile?.adapter === 'openclaw-gateway-rpc';
  const progress = {
    id: uid(),
    role: 'progress',
    title: `${activeAgentName()} 正在运行`,
    startedAt: Date.now(),
    statusText: isRpc ? '正在连接 OpenClaw Gateway RPC' : isOpenClaw ? '正在启动 OpenClaw CLI bridge' : '请求已发出，正在连接 Agent',
    steps: [
      { text: isRpc ? '连接 OpenClaw Gateway RPC' : isOpenClaw ? '启动 OpenClaw CLI bridge' : '发送请求到当前 Agent', state: 'active' },
      { text: isOpenClaw ? '绑定 OpenClaw session 与 agent' : 'Agent 整理上下文与附件', state: 'pending' },
      { text: isRpc ? '执行 Gateway agent RPC' : isOpenClaw ? '执行 openclaw agent 命令' : '等待模型生成结果', state: 'pending' },
      { text: '返回最终结果', state: 'pending' }
    ],
    meta: attachmentCount ? `${attachmentCount} 个附件 · 0.0s` : '0.0s'
  };
  chat.messages.push(progress);
  return progress;
}
function removeProgressMessage(chat, progressId){ const idx = chat.messages.findIndex(m => m.id === progressId); if(idx >= 0) chat.messages.splice(idx, 1); }
function completeProgress(chat, progress, text, isError){
  stopRunTicker(); updateRunStatus(null); removeProgressMessage(chat, progress.id);
  const elapsed = Date.now() - progress.startedAt;
  const successMeta = progress.finalMeta || `${activeAgentName()} · ${activeModelLabel()}`;
  chat.messages.push({ role: isError ? 'error' : 'assistant', content: text, meta: `${formatElapsed(elapsed)} · ${isError ? '执行失败' : successMeta}` });
}
function beginAssistantStream(chat, progress){
  stopRunTicker(); updateRunStatus(null); removeProgressMessage(chat, progress.id);
  const msg = { role: 'assistant', content: '', meta: `正在流式接收 · ${activeAgentName()} · ${activeModelLabel()}` };
  chat.messages.push(msg);
  return msg;
}
function appendAssistantDelta(msg, delta){ msg.content += delta; msg.meta = `正在流式接收 · ${activeAgentName()} · ${activeModelLabel()}`; renderMessages(); }
function finishAssistantStream(msg, started){ msg.meta = `${formatElapsed(Date.now() - started)} · ${activeAgentName()} · ${activeModelLabel()}`; }
function extractDeltaFromChunk(data){
  if(!data || data === '[DONE]') return '';
  const parsed = JSON.parse(data);
  if(parsed.choices?.[0]?.delta?.content) return parsed.choices[0].delta.content;
  if(parsed.choices?.[0]?.message?.content) return parsed.choices[0].message.content;
  if(parsed.output_text) return parsed.output_text;
  if(parsed.response?.output_text) return parsed.response.output_text;
  return '';
}
async function streamChat(payload, chat, progress, started){
  const r = await fetch('/api/chat-stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  if(!r.ok || !r.body) throw new Error(`流式请求失败：HTTP ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let assistantMsg = null;
  let receivedAny = false;

  while(true){
    const { value, done } = await reader.read();
    if(done) break;
    buffer += decoder.decode(value, {stream:true});
    const events = buffer.split('\n\n');
    buffer = events.pop() || '';

    for(const event of events){
      const lines = event.split('\n');
      const eventName = lines.find(line => line.startsWith('event:'))?.slice(6).trim() || 'message';
      const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
      if(!data || data === '[DONE]') continue;
      if(eventName === 'error') {
        const err = JSON.parse(data);
        throw new Error((err.error || '流式请求失败') + (err.detail ? '\n' + err.detail : ''));
      }
      if(eventName === 'final'){
        const final = JSON.parse(data);
        let text = final.choices?.[0]?.message?.content ?? JSON.stringify(final, null, 2);
        if(Array.isArray(text)) text = text.map(p=>p.text||JSON.stringify(p)).join('\n');
        if(!assistantMsg) assistantMsg = beginAssistantStream(chat, progress);
        assistantMsg.content += text;
        receivedAny = true;
        renderMessages();
        continue;
      }
      const delta = extractDeltaFromChunk(data);
      if(delta){
        if(!assistantMsg) assistantMsg = beginAssistantStream(chat, progress);
        appendAssistantDelta(assistantMsg, delta);
        receivedAny = true;
      }
    }
  }

  if(!receivedAny) throw new Error('流式接口没有返回内容');
  finishAssistantStream(assistantMsg, started);
  setBadge('ok', activeAgentName() + ' 在线');
}
async function nonStreamChat(payload, chat, progress, started){
  const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const d = await r.json();
  if(!r.ok || d.ok===false) throw new Error((d.error||'请求失败') + (d.detail?'\n'+d.detail:''));
  let reply = d.choices?.[0]?.message?.content ?? JSON.stringify(d,null,2);
  if(Array.isArray(reply)) reply = reply.map(p=>p.text||JSON.stringify(p)).join('\n');
  markStep(progress, 3, 'done');
  progress.statusText = 'Agent 已返回结果，正在整理展示';
  const detail = d._ui?.adapter === 'openclaw-cli'
    ? `OpenClaw CLI · agent:${d._ui?.agentId || 'main'} · session:${d._ui?.sessionId || payload.session_id || 'default'}`
    : d._ui?.adapter === 'openclaw-gateway-rpc'
    ? `OpenClaw Gateway RPC · agent:${d._ui?.agentId || 'main'} · session:${d._ui?.sessionId || payload.session_id || 'default'}`
    : '完成';
  progress.meta = `${formatElapsed(d._ui?.latencyMs ?? Date.now()-started)} · ${detail}`;
  progress.finalMeta = detail;
  completeProgress(chat, progress, reply, false);
  setBadge('ok', activeAgentName() + ' 在线');
}
function autoTitle(c, text){ if(c.title.startsWith('新的会话')) c.title = text.slice(0,28) || '附件分析'; }
async function readConfig(){
  if(isFileMode){ showBootNotice(true); return; }
  try{
    const r = await fetch('/api/config');
    const d = await r.json();
    if(d.agentHub){
      settings.agentProfiles = uniqueProfiles(d.agentHub.profiles || [], d.agentHub.activeAgentId).profiles;
      settings.discoveredAgents = uniqueProfiles(d.agentHub.discovered || []).profiles;
      settings.activeAgentId = d.agentHub.activeAgentId || settings.activeAgentId;
      settings.configPath = d.agentHub.configPath || '';
    }
    settings.baseUrl = d.defaultBaseUrl || settings.baseUrl;
    settings.model = d.defaultModel || settings.model;
    ensureHubDefaults();
    syncConnectionFromActiveProfile();
    syncSystemPrompts();
    save();
  }catch{}
}
async function discoverAgents(){
  if(isFileMode) return;
  const out = $('settingsOutput');
  const btn = $('discoverBtn');
  btn.disabled = true;
  btn.textContent = '扫描中…';
  out.textContent = 'Agent Hub 正在扫描本机 Hermes / OpenClaw…';
  $('discoverList').innerHTML = '<div class="discovery-empty">正在扫描本机配置、命令行工具和本地端口…</div>';
  try{
    const r = await fetch('/api/agents/discover');
    const d = await r.json();
    if(!r.ok || d.ok === false) throw new Error(d.error || `HTTP ${r.status}`);
    settings.discoveredAgents = uniqueProfiles(d.data || []).profiles;
    settings.configPath = d.configPath || settings.configPath;
    renderSettings();
    out.textContent = `扫描完成：发现 ${settings.discoveredAgents.length} 个 Agent，用时 ${formatElapsed(d.latencyMs || 0)}。\n\n` + JSON.stringify(d, null, 2);
    save();
  }catch(e){
    $('discoverList').innerHTML = '<div class="discovery-empty">扫描失败，请确认本地 UI 服务已经重启，并查看下方错误。</div>';
    out.textContent = '扫描失败：' + e.message;
  }finally{
    btn.disabled = false;
    btn.textContent = '重新扫描';
  }
}
function adapterLabel(adapter){
  if(adapter === 'openclaw-gateway-rpc') return 'OpenClaw Gateway RPC';
  if(adapter === 'openclaw-gateway') return 'OpenClaw Gateway';
  if(adapter === 'openclaw-cli') return 'OpenClaw CLI';
  return 'OpenAI-compatible';
}
function typeLabel(type){ return type === 'hermes' ? 'Hermes' : type === 'openclaw' ? 'OpenClaw' : 'Custom'; }
function sourceLabel(source){ return source === 'detected' ? '自动发现' : source === 'imported' ? '导入' : source === 'generated' ? '生成' : source === 'local-cache' ? '缓存' : '手动'; }
function profileStatusBadge(profile){
  if(profile.adapter === 'openclaw-gateway-rpc') return '<span class="pill ok">RPC</span>';
  if(profile.adapter === 'openclaw-gateway') return '<span class="pill warm">Gateway</span>';
  if(profile.adapter === 'openclaw-cli') return '<span class="pill warm">CLI</span>';
  if(profile.status === 'online' || profile.reachable === true) return '<span class="pill ok">在线</span>';
  if(profile.status) return `<span class="pill">${escapeHtml(profile.status)}</span>`;
  return '';
}
function renderSettings(){
  ensureHubDefaults();
  const active = getActiveProfile();
  if(!editingProfileId) editingProfileId = active?.id || null;
  const editing = getProfile(editingProfileId) || active;
  $('settingsMeta').textContent = settings.configPath
    ? `配置文件：${settings.configPath} · Agent Hub 支持自动发现本机 Hermes / OpenClaw，并保存为可复用的 Agent Profile。`
    : '可导入本机已有的 Hermes / OpenClaw，也可手动新增自定义 Agent。';
  renderProfileList();
  renderDiscoverList();
  fillProfileForm(editing);
  $('deleteProfileBtn').disabled = settings.agentProfiles.length <= 1;
}
function renderProfileList(){
  const root = $('profileList');
  root.innerHTML = settings.agentProfiles.map(profile => `
    <article class="profile-card ${profile.id === settings.activeAgentId ? 'active' : ''}">
      <div class="profile-card-head">
        <div>
          <strong>${escapeHtml(profile.label)}</strong>
          <div class="profile-sub">${escapeHtml(typeLabel(profile.type))} · ${escapeHtml(adapterLabel(profile.adapter))} · ${escapeHtml(sourceLabel(profile.source))}</div>
        </div>
        <div class="profile-pills">
          ${profile.id === settings.activeAgentId ? '<span class="pill active">当前</span>' : ''}
          ${profileStatusBadge(profile)}
        </div>
      </div>
      <div class="profile-meta">${escapeHtml(profile.baseUrl || profile.binaryPath || '未设置地址')} ${profile.agentId ? `· agent:${escapeHtml(profile.agentId)}` : ''} ${profile.model ? `· ${escapeHtml(profile.model)}` : ''}</div>
      ${profile.notes ? `<div class="profile-note">${escapeHtml(profile.notes)}</div>` : ''}
      <div class="profile-actions-inline">
        <button data-action="select" data-id="${profile.id}">设为当前</button>
        <button data-action="edit" data-id="${profile.id}">编辑</button>
        <button data-action="clone" data-id="${profile.id}">复制</button>
      </div>
    </article>
  `).join('');
  root.querySelectorAll('button[data-action]').forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      const action = btn.dataset.action;
      if(action === 'select') await setActiveProfile(id);
      if(action === 'edit') { editingProfileId = id; fillProfileForm(getProfile(id)); }
      if(action === 'clone') {
        const src = getProfile(id);
        const copy = normalizeProfile({...src, id: profileId(), label: `${src.label} 副本`, source: 'manual'});
        settings.agentProfiles.unshift(copy);
        editingProfileId = copy.id;
        fillProfileForm(copy);
        save();
        renderSettings();
      }
    };
  });
}
function renderDiscoverList(){
  const root = $('discoverList');
  if(!settings.discoveredAgents.length){
    root.innerHTML = '<div class="discovery-empty">暂未扫描到本机 Agent。你也可以直接手动添加。</div>';
    return;
  }
  root.innerHTML = settings.discoveredAgents.map(profile => `
    <article class="discover-card ${profile.adapter !== 'openai-chat' ? 'gateway' : ''}">
      <div class="profile-card-head">
        <div>
          <strong>${escapeHtml(profile.label)}</strong>
          <div class="profile-sub">${escapeHtml(typeLabel(profile.type))} · ${escapeHtml(adapterLabel(profile.adapter))}</div>
        </div>
        <div class="profile-pills">
          ${profileStatusBadge(profile)}
        </div>
      </div>
      <div class="profile-meta">${escapeHtml(profile.baseUrl || profile.binaryPath || profile.configSource || '未找到连接地址')} ${profile.agentId ? `· agent:${escapeHtml(profile.agentId)}` : ''}</div>
      <div class="profile-note">${escapeHtml(profile.notes || '')}</div>
      <div class="profile-actions-inline">
        <button data-action="import" data-id="${profile.id}">导入到 Hub</button>
        <button data-action="inspect" data-id="${profile.id}">填入表单</button>
      </div>
    </article>
  `).join('');
  root.querySelectorAll('button[data-action]').forEach(btn => {
    btn.onclick = async () => {
      const found = settings.discoveredAgents.find(p => p.id === btn.dataset.id);
      if(!found) return;
      if(btn.dataset.action === 'inspect'){
        editingProfileId = null;
        fillProfileForm(found);
        return;
      }
      const foundKey = agentProfileKey(found);
      const existing = settings.agentProfiles.find(p => agentProfileKey(p) === foundKey);
      const profile = normalizeProfile(existing ? {...existing, ...found, source:'imported'} : {...found, id: profileId(), source:'imported'});
      if(existing){
        settings.agentProfiles = settings.agentProfiles.map(p => p.id === existing.id ? profile : p);
        editingProfileId = profile.id;
      } else {
        settings.agentProfiles.unshift(profile);
        editingProfileId = profile.id;
      }
      settings.activeAgentId = profile.id;
      syncConnectionFromActiveProfile();
      await persistAgentHub();
      renderSettings();
      $('settingsOutput').textContent = `已导入 ${profile.label}。`;
    };
  });
}
function fillProfileForm(profile){
  const draft = normalizeProfile(profile || {
    label: '', type: 'custom', adapter: 'openai-chat', baseUrl: 'http://127.0.0.1:8642/v1', model: '', apiKey: '', agentId: '', notes: ''
  });
  $('profileLabel').value = draft.label || '';
  $('profileType').value = draft.type || 'custom';
  $('profileAdapter').value = draft.adapter || 'openai-chat';
  $('baseUrl').value = draft.baseUrl || '';
  $('profileAgentId').value = draft.agentId || '';
  $('model').value = draft.model || '';
  $('apiKey').value = draft.apiKey || '';
  $('profileNotes').value = draft.notes || '';
}
function profileFromForm(){
  const existing = editingProfileId ? getProfile(editingProfileId) : null;
  return normalizeProfile({
    id: existing?.id || profileId(),
    label: $('profileLabel').value.trim() || '未命名 Agent',
    type: $('profileType').value,
    adapter: $('profileAdapter').value,
    baseUrl: $('baseUrl').value.trim(),
    agentId: $('profileAgentId').value.trim(),
    model: $('model').value.trim(),
    apiKey: $('apiKey').value.trim(),
    notes: $('profileNotes').value.trim(),
    supportsStream: true,
    supportsVision: true,
    source: existing?.source || 'manual'
  });
}
async function persistAgentHub(){
  save();
  if(isFileMode) return;
  const payload = { profiles: settings.agentProfiles, activeAgentId: settings.activeAgentId };
  const r = await fetch('/api/agents/save', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const d = await r.json();
  if(!r.ok || d.ok === false) throw new Error(d.error || '保存失败');
  settings.agentProfiles = (d.agentHub?.profiles || settings.agentProfiles).map(normalizeProfile).filter(Boolean);
  settings.activeAgentId = d.agentHub?.activeAgentId || settings.activeAgentId;
  settings.configPath = d.configPath || settings.configPath;
  save();
}
async function setActiveProfile(id){
  settings.activeAgentId = id;
  editingProfileId = id;
  syncConnectionFromActiveProfile();
  syncSystemPrompts();
  renderAll();
  try{ await persistAgentHub(); }catch(e){ $('settingsOutput').textContent = '切换当前 Agent 保存失败：' + e.message; }
  await health();
}
async function saveCurrentProfile(){
  const out = $('settingsOutput');
  const profile = profileFromForm();
  if(!profile.baseUrl){ out.textContent = '请填写 Base URL / Gateway URL。'; return; }
  const idx = settings.agentProfiles.findIndex(p => p.id === profile.id);
  if(idx >= 0) settings.agentProfiles[idx] = profile;
  else settings.agentProfiles.unshift(profile);
  settings.activeAgentId = profile.id;
  editingProfileId = profile.id;
  syncConnectionFromActiveProfile();
  syncSystemPrompts();
  try{
    await persistAgentHub();
    out.textContent = `已保存 ${profile.label}。`;
    $('settingsModal').classList.add('hidden');
    renderAll();
    await health();
  }catch(e){ out.textContent = '保存失败：' + e.message; }
}
async function deleteCurrentProfile(){
  const targetId = editingProfileId || settings.activeAgentId;
  if(settings.agentProfiles.length <= 1){ $('settingsOutput').textContent = '至少保留一个 Agent profile。'; return; }
  settings.agentProfiles = settings.agentProfiles.filter(p => p.id !== targetId);
  if(settings.activeAgentId === targetId) settings.activeAgentId = settings.agentProfiles[0]?.id || null;
  editingProfileId = settings.activeAgentId;
  syncConnectionFromActiveProfile();
  syncSystemPrompts();
  try{
    await persistAgentHub();
    renderSettings();
    await health();
  }catch(e){ $('settingsOutput').textContent = '删除失败：' + e.message; }
}
function startNewProfile(){
  editingProfileId = null;
  fillProfileForm({
    label: '',
    type: 'custom',
    adapter: 'openai-chat',
    baseUrl: 'http://127.0.0.1:8642/v1',
    agentId: '',
    model: '',
    apiKey: '',
    notes: ''
  });
  $('settingsOutput').textContent = '已切换到新建模式。';
}
async function health(){
  if(isFileMode){
    setBadge('bad','需启动本地服务');
    return {ok:false, error:'preview-mode', hint:`请启动 ${localProxyUrl} 的 Agent Hub 本地服务，由它代理访问 Agent，避免浏览器直连本地端口时触发 CORS / 本地文件限制。`};
  }
  setBadge('unknown','检测中');
  try{
    const headers = {};
    const profile = getActiveProfile();
    if(profile?.adapter === 'openai-chat' && profile.baseUrl) headers['X-Hermes-Base'] = profile.baseUrl;
    const r = await fetch('/api/health', {headers});
    const d = await r.json();
    if(profile?.adapter === 'openclaw-gateway'){
      setBadge('unknown','Gateway 待接入');
    } else {
      setBadge(r.ok && d.ok ? 'ok' : 'bad', r.ok && d.ok ? `${activeAgentName()} 在线` : `${activeAgentName()} 异常`);
    }
    return d;
  }catch(e){ setBadge('bad','连接失败'); return {ok:false,error:e.message}; }
}
async function loadModels(){
  const out=$('settingsOutput'); out.textContent='正在读取模型…';
  if(isFileMode){
    out.textContent = `当前是 file:// 预览模式。\n\n请先启动 Agent Hub 本地服务：\ncd agent-hub && HERMES_BASE_URL=http://127.0.0.1:8642/v1 HERMES_MODEL=hermes-agent ./run.sh\n\n然后通过 ${localProxyUrl} 打开页面，再读取模型。`;
    return;
  }
  if($('profileAdapter').value !== 'openai-chat'){
    out.textContent = '当前连接方式不是 OpenAI-compatible HTTP，暂不支持读取 /v1/models。';
    return;
  }
  try{
    const h={'X-Hermes-Base':$('baseUrl').value.trim()}; if($('apiKey').value) h['X-Hermes-Key']=$('apiKey').value;
    const r = await fetch('/api/models',{headers:h}); const d = await r.json(); out.textContent=JSON.stringify(d,null,2);
    const id = d.data?.[0]?.id; if(id) $('model').value = id;
  }catch(e){ out.textContent='读取失败：'+e.message; }
}
function fileToDataURL(file){ return new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res(r.result); r.onerror=rej; r.readAsDataURL(file); }); }
async function fileToAttachment(file){
  if(file.type.startsWith('image/')) return {kind:'image', name:file.name, mime:file.type||'image/png', dataUrl:await fileToDataURL(file)};
  if(file.type.startsWith('text/') || textExt.test(file.name)){
    let text = await file.text(); if(text.length>30000) text = text.slice(0,30000)+'\n\n[文件过长，已截断]';
    return {kind:'text', name:file.name, mime:file.type||'text/plain', text};
  }
  return {kind:'file', name:file.name, mime:file.type||'application/octet-stream'};
}
async function addFiles(files){ for(const f of files) attachments.push(await fileToAttachment(f)); renderAttachments(); }
function renderAttachments(){
  $('attachments').innerHTML='';
  attachments.forEach((a,i)=>{
    const el=document.createElement('div'); el.className='att';
    el.innerHTML = `${a.kind==='image'?`<img src="${a.dataUrl}" />`:'📄'}<span>${escapeHtml(a.name)}</span><button>×</button>`;
    el.querySelector('button').onclick=()=>{ attachments.splice(i,1); renderAttachments(); };
    $('attachments').appendChild(el);
  });
}
function buildContent(text){
  const textParts=[]; if(text) textParts.push(text);
  for(const a of attachments){
    if(a.kind==='text') textParts.push(`\n\n[附件：${a.name}]\n\`\`\`\n${a.text}\n\`\`\``);
    if(a.kind==='file') textParts.push(`\n\n[附件文件：${a.name}，MIME=${a.mime}。当前版本不解析二进制文件，请上传文本版或让后端工具读取。]`);
  }
  const joined = textParts.join('\n').trim() || '请分析附件。';
  if(attachments.some(a=>a.kind==='image')){
    const parts=[{type:'text', text:joined}];
    attachments.filter(a=>a.kind==='image').forEach(a=>parts.push({type:'image_url', image_url:{url:a.dataUrl}}));
    return parts;
  }
  return joined;
}
async function send(){
  if(busy) return;
  const text = $('input').value.trim();
  if(!text && !attachments.length) return;
  if(isFileMode){
    const c = activeChat();
    c.messages.push({role:'error', content:`当前是 file:// 预览模式，不能直接发送请求。\n\n请先启动本地 UI 服务，再通过 ${localProxyUrl} 打开。这样所有浏览器请求都会走本地代理，不会直连 Agent 端口。`, meta:'预览模式限制'});
    save(); renderAll(); setBadge('bad','需启动本地服务'); showBootNotice(true); return;
  }
  const profile = getActiveProfile();
  if(profile?.adapter === 'openclaw-gateway'){
    const c = activeChat();
    c.messages.push({role:'error', content:`当前选中的是 ${profile.label}。\n\n它走的是 ${adapterLabel(profile.adapter)}，当前版本已经支持发现、导入、配置和切换，但还没有把执行桥接层接通。\n\n建议先切换到 Hermes profile，或者下一步我继续把 OpenClaw Gateway adapter 接上。`, meta:'Agent Hub 提示'});
    save(); renderAll(); setBadge('unknown','Gateway 待接入');
    return;
  }
  const c = activeChat();
  const content = buildContent(text);
  const attachmentCount = attachments.length;
  c.messages.push({role:'user', content, meta: attachments.length ? `${attachments.length} 个附件` : ''});
  autoTitle(c, text || '附件分析');
  $('input').value=''; attachments=[]; renderAttachments();
  const progress = createProgressMessage(c, attachmentCount);
  activeProgressId = progress.id;
  save(); renderAll(); setBusy(true); updateRunStatus(progress); startRunTicker(c.id, progress.id);
  runPhasesForProfile(profile, c.id).forEach((phase, index) => {
    window.setTimeout(() => {
      const chat = chats.find(item => item.id === c.id);
      const current = chat?.messages.find(m => m.id === progress.id);
      if(!current) return;
      current.statusText = phase.text;
      if(index === 1) markStep(current, 1, 'active');
      if(index === 2) markStep(current, 2, 'active');
      if(index === 3) markStep(current, 2, 'active');
      save();
      updateRunStatus(current);
      renderMessages();
    }, phase.delay);
  });
  const started=Date.now();
  try{
    const apiMessages = messagesForApi(c);
    progress.meta = `${summarizeApiContext(apiMessages)} · ${formatElapsed(Date.now() - progress.startedAt)}`;
    renderMessages();
    const payload = {base_url:settings.baseUrl, api_key:settings.apiKey, model:settings.model, messages:apiMessages, temperature:0.3, stream:true, session_id:c.id};
    if(isOpenClawAdapter(profile)){
      progress.statusText = profile.adapter === 'openclaw-gateway-rpc'
        ? `正在执行 Gateway RPC agent:${profile.agentId || 'main'}`
        : `正在执行 openclaw agent --agent ${profile.agentId || 'main'} --json`;
      markStep(progress, 2, 'active');
      progress.meta = `${profile.adapter === 'openclaw-gateway-rpc' ? 'Gateway RPC' : 'CLI bridge'} · session:${c.id} · ${formatElapsed(Date.now() - progress.startedAt)}`;
      updateRunStatus(progress);
      renderMessages();
      await nonStreamChat({...payload, stream:false}, c, progress, started);
    } else {
      try {
        await streamChat(payload, c, progress, started);
      } catch(streamError) {
        if(c.messages.find(m => m.id === progress.id)) {
          progress.statusText = '流式不可用，切换为普通响应模式';
          renderMessages();
          await nonStreamChat({...payload, stream:false}, c, progress, started);
        } else {
          throw streamError;
        }
      }
    }
  }catch(e){
    progress.statusText = '执行失败，请检查当前 Agent 或上游模型配置';
    if(c.messages.find(m => m.id === progress.id)) completeProgress(c, progress, '调用失败：'+e.message, true);
    else c.messages.push({role:'error', content:'调用失败：'+e.message, meta:`${formatElapsed(Date.now() - started)} · 执行失败`});
    setBadge('bad','调用失败');
  }finally{ activeProgressId = null; save(); renderAll(); setBusy(false); }
}
function exportMd(){
  const c=activeChat(); const lines=[`# ${c.title}\n`];
  c.messages.filter(m=>m.role!=='system').forEach(m=>lines.push(`## ${m.role}\n\n${displayContent(m.content)}\n`));
  const blob=new Blob([lines.join('\n')],{type:'text/markdown'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=(c.title||'agent-chat')+'.md'; a.click();
}

$('newChat').onclick=()=>createChat(true);
$('searchChat').oninput=renderChatList;
$('settingsBtn').onclick=()=>{$('settingsModal').classList.remove('hidden'); renderSettings();};
$('closeSettings').onclick=()=>$('settingsModal').classList.add('hidden');
$('saveSettings').onclick=saveCurrentProfile;
$('deleteProfileBtn').onclick=deleteCurrentProfile;
$('modelsBtn').onclick=loadModels;
$('healthBtn').onclick=async()=>{ const d=await health(); alert(JSON.stringify(d,null,2)); };
$('discoverBtn').onclick=discoverAgents;
$('newProfileBtn').onclick=startNewProfile;
$('exportBtn').onclick=exportMd;
$('attachBtn').onclick=()=>$('fileInput').click();
$('fileInput').onchange=e=>addFiles(e.target.files);
$('sendBtn').onclick=send;
$('input').addEventListener('keydown', e=>{ if((e.metaKey||e.ctrlKey)&&e.key==='Enter') send(); });
for(const b of document.querySelectorAll('.prompt-card')) b.onclick=()=>{ $('input').value=b.textContent; $('input').focus(); };
window.addEventListener('dragover', e=>{ e.preventDefault(); document.body.classList.add('dragging'); });
window.addEventListener('dragleave', ()=>document.body.classList.remove('dragging'));
window.addEventListener('drop', e=>{ e.preventDefault(); document.body.classList.remove('dragging'); addFiles(e.dataTransfer.files); });
$('profileAdapter').addEventListener('change', () => {
  const adapter = $('profileAdapter').value;
  if(adapter === 'openclaw-gateway' && !$('baseUrl').value.trim()) $('baseUrl').value = 'ws://127.0.0.1:18789';
  if(adapter === 'openclaw-gateway-rpc'){
    if(!$('profileAgentId').value.trim()) $('profileAgentId').value = 'main';
    if(!$('baseUrl').value.trim()) $('baseUrl').value = 'ws://127.0.0.1:18789';
  }
  if(adapter === 'openclaw-cli'){
    if(!$('profileAgentId').value.trim()) $('profileAgentId').value = 'main';
    if(!$('baseUrl').value.trim()) $('baseUrl').value = 'openclaw';
  }
});

(async function init(){
  load();
  await readConfig();
  renderAll();
  const result = await health();
  if(isFileMode || result?.error === 'preview-mode') showBootNotice(true);
})();
