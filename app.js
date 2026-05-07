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
let serverSessionsReady = false;
let sessionSaveTimer = null;
let agentRuns = [];
let handoffRecords = [];
let contextPackage = null;
let taskSpec = null;
let taskWorkflow = null;
let latestWorkflowAction = null;
let projectMemories = [];
let settingsTab = 'overview';
let taskFilter = 'all';
let taskSort = 'updated_desc';
let currentAgentOnly = false;
let taskChromeCollapsed = true;
let taskChromeScale = 'compact';
let taskChromePinned = true;
let taskRecommendation = null;
let taskRecommendationBusy = false;
let modelRegistry = {};
let activeAbortController = null;
let activeRun = null;
const isFileMode = window.location.protocol === 'file:';
const localProxyUrl = 'http://127.0.0.1:8765';
const MAX_CONTEXT_MESSAGES = 8;
const MAX_CONTEXT_CHARS = 6000;
const MAX_SINGLE_MESSAGE_CHARS = 3000;
const AGENT_RUN_TIMEOUT_SECONDS = 900;
const richDocExt = /\.(pdf|docx|xlsx)$/i;
const runPhases = [
  { delay: 0, text: '请求已发出，正在连接 Agent' },
  { delay: 900, text: 'Agent 已接收任务，正在整理上下文' },
  { delay: 2200, text: '正在等待上游模型响应' },
  { delay: 5200, text: '任务耗时较长，Agent 仍在执行中' }
];
function isOpenClawAdapter(profile){
  return AgentHubAgents.isOpenClawAdapter(profile);
}
function isClaudeAdapter(profile){
  return AgentHubAgents.isClaudeAdapter(profile);
}
function runPhasesForProfile(profile, sessionId){
  return AgentHubAgents.runPhasesForProfile(profile, sessionId, runPhases);
}

function uid(){ return 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2,8); }
function profileId(){ return 'agent_' + Date.now().toString(36) + Math.random().toString(36).slice(2,7); }
function nowTitle(){ return new Date().toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}); }
function escapeHtml(s){ return String(s ?? '').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function stripPersistedContent(content){
  return AgentHubState.stripPersistedContent(content);
}
function chatsForStorage(){
  return AgentHubState.chatsForStorage(chats);
}
function settingsForStorage(){
  return AgentHubState.settingsForStorage(settings, {taskSort, currentAgentOnly, taskChromeCollapsed, taskChromeScale, taskChromePinned, modelRegistry});
}
function save(){
  try{
    AgentHubState.saveLocal(storeKey, AgentHubState.snapshot({settings, chats, activeId, taskSort, currentAgentOnly, taskChromeCollapsed, taskChromeScale, taskChromePinned, modelRegistry}));
  }catch(e){
    console.warn('Agent Hub localStorage save skipped:', e.message);
  }
  scheduleSessionSync();
}
function sessionsForServer(){
  return AgentHubState.sessionsForServer({settings, chats, activeId, taskSort, currentAgentOnly}).sessions;
}
function scheduleSessionSync(){
  if(isFileMode || !serverSessionsReady) return;
  clearTimeout(sessionSaveTimer);
  sessionSaveTimer = setTimeout(() => syncSessionsToServer().catch(e => console.warn('Agent Hub session sync skipped:', e.message)), 700);
}
async function syncSessionsToServer(){
  if(isFileMode || !serverSessionsReady) return;
  const payload = {sessions: sessionsForServer(), activeId, activeAgentId: settings.activeAgentId};
  await AgentHubApi.syncSessions(payload);
}
async function loadSessionsFromServer(){
  if(isFileMode) return;
  try{
    const d = await AgentHubApi.sessions();
    const remote = d.data || {};
    if(Array.isArray(remote.sessions) && remote.sessions.length){
      chats = remote.sessions.map(chat => ({
        id: chat.id,
        title: chat.title || '新的会话',
        createdAt: chat.createdAt || Date.now(),
        updatedAt: chat.updatedAt || Date.now(),
        taskGoal: chat.taskGoal || '',
        taskStatus: chat.taskStatus || 'active',
        nextStep: chat.nextStep || '',
        workspacePath: chat.workspacePath || '',
        workspaceName: chat.workspaceName || '',
        workspaceGitRepo: !!chat.workspaceGitRepo,
        workspaceGitBranch: chat.workspaceGitBranch || '',
        workspaceGitDirty: !!chat.workspaceGitDirty,
        workspaceCheckedAt: chat.workspaceCheckedAt || 0,
        approvalPolicy: chat.approvalPolicy || 'auto',
        contextStrategy: chat.contextStrategy || 'standard',
        summary: chat.summary || '',
        activeAgentId: chat.activeAgentId || settings.activeAgentId,
        artifacts: chat.artifacts || [],
        handoffSummary: chat.handoffSummary || '',
        handoffFromAgentId: chat.handoffFromAgentId || '',
        handoffFromLabel: chat.handoffFromLabel || '',
        handoffToAgentId: chat.handoffToAgentId || '',
        handoffToLabel: chat.handoffToLabel || '',
        handoffAt: chat.handoffAt || 0,
        handoffDismissed: !!chat.handoffDismissed,
        messages: chat.messages || [systemMessage()]
      }));
      activeId = remote.activeId || chats[0].id;
    } else if(chats.length) {
      serverSessionsReady = true;
      await syncSessionsToServer();
    }
    if(!chats.length) createChat(false);
    if(!activeId || !chats.find(c=>c.id===activeId)) activeId = chats[0].id;
  }catch(e){
    console.warn('Agent Hub server sessions unavailable:', e.message);
  }finally{
    serverSessionsReady = !isFileMode;
  }
}
function agentProfileKey(profile){
  return AgentHubAgents.agentProfileKey(profile);
}
function uniqueProfiles(list, activeId = null){
  return AgentHubAgents.uniqueProfiles(list, activeId, profileId);
}
function normalizeProfile(profile){
  return AgentHubAgents.normalizeProfile(profile, profileId);
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
  const raw = AgentHubState.loadLocal(storeKey);
  settings = {...settings, ...(raw.settings||{})};
  taskSort = raw.settings?.taskSort || taskSort;
  currentAgentOnly = !!raw.settings?.currentAgentOnly;
  taskChromeCollapsed = raw.settings?.taskChromeCollapsed !== false;
  taskChromeScale = raw.settings?.taskChromeScale || taskChromeScale;
  taskChromePinned = raw.settings?.taskChromePinned !== false;
  modelRegistry = raw.settings?.modelRegistry && typeof raw.settings.modelRegistry === 'object' ? raw.settings.modelRegistry : {};
  chats = raw.chats || [];
  activeId = raw.activeId || null;
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
function agentModeSuffix(profile){
  if(!profile) return '';
  if(profile.adapter === 'openclaw-gateway-rpc') return 'Gateway RPC';
  if(profile.adapter === 'openclaw-gateway') return 'Gateway';
  if(profile.adapter === 'openclaw-cli') return 'CLI';
  if(profile.adapter === 'claude-code-cli') return 'Claude CLI';
  if(profile.adapter === 'openai-chat') return 'OpenAI-compatible';
  return adapterLabel(profile.adapter);
}
function systemMessage(){ return {role:'system', content:`你是 ${activeAgentName()} 企业智能体。请用中文、结构化、专业但简洁的方式回答。用户可能上传图片或文本附件，请结合附件内容。`}; }
function createChat(render=true){
  const now = Date.now();
  const c = {id:uid(), title:'新的会话 ' + nowTitle(), createdAt:now, updatedAt:now, activeAgentId:settings.activeAgentId, pinned:false, taskGoal:'', taskStatus:'todo', nextStep:'', workspacePath:'', workspaceName:'', workspaceGitRepo:false, workspaceGitBranch:'', workspaceGitDirty:false, workspaceCheckedAt:0, approvalPolicy:'auto', contextStrategy:'standard', summary:'', handoffSummary:'', handoffFromAgentId:'', handoffFromLabel:'', handoffToAgentId:'', handoffToLabel:'', handoffAt:0, handoffDismissed:false, artifacts:[], messages:[systemMessage()]};
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
  renderAgentSwitcher();
  renderModelSwitcher();
  $('subTitle').textContent = profile
    ? `${profile.label} · ${adapterLabel(profile.adapter)} · ${agentCapabilityLabel(profile)} · 浏览器经本地服务代理访问`
    : 'Agent Hub · 多模态入口 · 浏览器经本地服务代理访问';
}
function modelOptionLabel(value){
  return String(value || '').trim() || '未设置模型';
}
function modelRegistryKey(profile = getActiveProfile()){
  if(!profile) return 'global';
  const adapter = profile.adapter || 'custom';
  const endpoint = profile.baseUrl || profile.binaryPath || profile.agentId || profile.id || 'local';
  return `${profile.id || adapter}|${adapter}|${endpoint}`;
}
function defaultModelOptionsForProfile(profile){
  const adapter = profile?.adapter || '';
  const type = profile?.type || '';
  if(adapter === 'claude-code-cli' || type === 'claude') return ['sonnet', 'opus', 'default'];
  if(adapter === 'openclaw-gateway-rpc' || adapter === 'openclaw-cli' || type === 'openclaw') {
    return [profile?.model || 'openclaw-agent'].filter(Boolean);
  }
  if(type === 'hermes') return [profile?.model || 'hermes-agent'].filter(Boolean);
  return [profile?.model || settings.model || 'model'].filter(Boolean);
}
function modelRegistryEntry(profile = getActiveProfile()){
  const key = modelRegistryKey(profile);
  if(!modelRegistry[key]){
    modelRegistry[key] = {
      providerKey: key,
      agentId: profile?.id || '',
      adapter: profile?.adapter || '',
      label: profile?.label || '',
      endpoint: profile?.baseUrl || profile?.binaryPath || '',
      models: [],
      fetchedAt: 0,
      source: 'local',
    };
  }
  return modelRegistry[key];
}
function collectModelOptions(){
  const set = new Set();
  const profile = getActiveProfile();
  if(profile?.model) set.add(profile.model);
  const entry = modelRegistryEntry(profile);
  (entry.models || []).forEach(item => item && set.add(item));
  defaultModelOptionsForProfile(profile).forEach(item => item && set.add(item));
  return [...set];
}
function renderModelSwitcher(){
  const button = $('modelMode');
  const label = $('modelModeLabel');
  const menu = $('modelSwitchMenu');
  if(!button || !label || !menu) return;
  const profile = getActiveProfile();
  const current = profile?.model || settings.model || '';
  const entry = modelRegistryEntry(profile);
  label.textContent = modelOptionLabel(current);
  button.title = `当前模型：${modelOptionLabel(current)}。模型候选仅属于当前 Agent。`;
  const options = collectModelOptions();
  menu.innerHTML = `
    <div class="model-switch-head">
      <strong>${escapeHtml(profile?.label || '当前 Agent')}</strong>
      <small>${escapeHtml(profile?.adapter || 'adapter')} · ${entry.fetchedAt ? `已读取 ${new Date(entry.fetchedAt).toLocaleTimeString('zh-CN')}` : '本地候选'}</small>
    </div>
    ${options.map(value => `
      <button class="model-switch-option ${value === current ? 'active' : ''}" data-model="${escapeHtml(value)}" role="option" aria-selected="${value === current}">
        <span>
          <strong>${escapeHtml(modelOptionLabel(value))}</strong>
          <small>${escapeHtml(value === current ? '当前模型' : '仅切换当前 Agent 的模型')}</small>
        </span>
        ${value === current ? '<b>✓</b>' : ''}
      </button>
    `).join('')}
    <div class="model-switch-actions">
      <button type="button" data-model-action="load">读取 /models</button>
      <button type="button" data-model-action="custom">自定义</button>
    </div>
  `;
  menu.querySelectorAll('.model-switch-option').forEach(btn => {
    btn.onclick = async event => {
      event.stopPropagation();
      closeModelSwitcher();
      await setActiveModel(btn.dataset.model || '');
    };
  });
  menu.querySelectorAll('[data-model-action]').forEach(btn => {
    btn.onclick = async event => {
      event.stopPropagation();
      if(btn.dataset.modelAction === 'load') await refreshModelOptions();
      if(btn.dataset.modelAction === 'custom') await promptCustomModel();
    };
  });
}
function modelRegistrySourceLabel(entry){
  if(!entry?.fetchedAt) return '本地候选';
  return `${entry.source || 'models'} · ${new Date(entry.fetchedAt).toLocaleString('zh-CN')}`;
}
function renderModelRegistryPanel(){
  const root = $('modelRegistryPanel');
  if(!root) return;
  const activeId = settings.activeAgentId;
  root.innerHTML = `
    <div class="model-registry-grid">
      ${(settings.agentProfiles || []).map(profile => {
        const entry = modelRegistryEntry(profile);
        const options = [...new Set([profile.model || '', ...(entry.models || []), ...defaultModelOptionsForProfile(profile)].filter(Boolean))];
        const canRead = profile.adapter === 'openai-chat';
        return `
          <article class="model-registry-card ${profile.id === activeId ? 'active' : ''}">
            <div class="model-registry-head">
              <div>
                <strong>${escapeHtml(profile.label || 'Agent')}</strong>
                <p>${escapeHtml(adapterLabel(profile.adapter))} · ${escapeHtml(profile.baseUrl || profile.binaryPath || profile.agentId || 'local')}</p>
              </div>
              ${profile.id === activeId ? '<span class="pill active">当前 Agent</span>' : ''}
            </div>
            <div class="model-registry-current">
              <span>绑定模型</span>
              <select data-model-agent="${escapeHtml(profile.id)}">
                ${options.map(model => `<option value="${escapeHtml(model)}" ${model === profile.model ? 'selected' : ''}>${escapeHtml(model)}</option>`).join('')}
              </select>
            </div>
            <div class="model-registry-meta">
              <span>${escapeHtml(modelRegistrySourceLabel(entry))}</span>
              <span>${options.length} 个候选</span>
              ${canRead ? '<span>支持 /models</span>' : '<span>手动候选</span>'}
            </div>
            <div class="model-registry-options">
              ${options.map(model => `<button type="button" data-model-agent="${escapeHtml(profile.id)}" data-model-value="${escapeHtml(model)}" class="${model === profile.model ? 'active' : ''}">${escapeHtml(model)}</button>`).join('')}
            </div>
            <div class="profile-actions-inline">
              <button type="button" data-model-action="activate" data-agent-id="${escapeHtml(profile.id)}">设为当前 Agent</button>
              ${canRead ? `<button type="button" data-model-action="read" data-agent-id="${escapeHtml(profile.id)}">读取模型</button>` : ''}
              <button type="button" data-model-action="custom" data-agent-id="${escapeHtml(profile.id)}">添加模型</button>
            </div>
          </article>
        `;
      }).join('')}
    </div>
  `;
  root.querySelectorAll('select[data-model-agent]').forEach(select => {
    select.onchange = () => setProfileModel(select.dataset.modelAgent, select.value);
  });
  root.querySelectorAll('button[data-model-value]').forEach(btn => {
    btn.onclick = () => setProfileModel(btn.dataset.modelAgent, btn.dataset.modelValue);
  });
  root.querySelectorAll('button[data-model-action]').forEach(btn => {
    btn.onclick = async () => {
      const agentId = btn.dataset.agentId;
      if(btn.dataset.modelAction === 'activate') await setActiveProfile(agentId);
      if(btn.dataset.modelAction === 'read') await refreshModelOptionsForProfile(agentId);
      if(btn.dataset.modelAction === 'custom') await promptCustomModelForProfile(agentId);
    };
  });
}
async function setProfileModel(profileId, model){
  const profile = getProfile(profileId);
  const value = String(model || '').trim();
  if(!profile || !value) return;
  profile.model = value;
  const entry = modelRegistryEntry(profile);
  entry.models = [...new Set([value, ...(entry.models || [])])];
  if(profile.id === settings.activeAgentId) settings.model = value;
  syncConnectionFromActiveProfile();
  save();
  try{ await persistAgentHub(); }catch(e){ console.warn('Agent Hub profile model save skipped:', e.message); }
  renderAll();
}
async function refreshModelOptionsForProfile(profileId){
  const profile = getProfile(profileId);
  if(!profile) return;
  if(profile.adapter !== 'openai-chat'){
    alert('该 Agent 不是 OpenAI-compatible HTTP，不能读取 /v1/models。可以添加自定义模型。');
    return;
  }
  const oldActive = settings.activeAgentId;
  try{
    settings.activeAgentId = profile.id;
    syncConnectionFromActiveProfile();
    const h = {'X-Hermes-Base':settings.baseUrl, 'X-Hermes-Key':settings.apiKey || ''};
    const d = await AgentHubApi.models(h);
    const ids = (d.data || []).map(item => item.id).filter(Boolean);
    const entry = modelRegistryEntry(profile);
    entry.models = [...new Set([profile.model || '', ...ids].filter(Boolean))];
    entry.fetchedAt = Date.now();
    entry.source = '/v1/models';
    save();
  }catch(e){
    alert('读取模型失败：' + e.message);
  }finally{
    settings.activeAgentId = oldActive;
    syncConnectionFromActiveProfile();
    renderAll();
  }
}
async function promptCustomModelForProfile(profileId){
  const profile = getProfile(profileId);
  if(!profile) return;
  const next = window.prompt(`为「${profile.label}」添加模型：`, profile.model || '');
  if(next === null) return;
  await setProfileModel(profile.id, next);
}
function isModelSwitcherOpen(){
  return !$('modelSwitchMenu')?.classList.contains('hidden');
}
function openModelSwitcher(){
  renderModelSwitcher();
  $('modelSwitchMenu')?.classList.remove('hidden');
  $('modelSwitcher')?.classList.add('open');
  $('modelMode')?.setAttribute('aria-expanded', 'true');
}
function closeModelSwitcher(){
  $('modelSwitchMenu')?.classList.add('hidden');
  $('modelSwitcher')?.classList.remove('open');
  $('modelMode')?.setAttribute('aria-expanded', 'false');
}
function toggleModelSwitcher(){
  if(isModelSwitcherOpen()) closeModelSwitcher();
  else openModelSwitcher();
}
async function setActiveModel(model){
  const value = String(model || '').trim();
  if(!value) return;
  const profile = getActiveProfile();
  if(profile) profile.model = value;
  settings.model = value;
  const entry = modelRegistryEntry(profile);
  entry.models = [...new Set([value, ...(entry.models || [])])];
  syncConnectionFromActiveProfile();
  save();
  try{ await persistAgentHub(); }catch(e){ console.warn('Agent Hub model save skipped:', e.message); }
  renderAll();
}
async function refreshModelOptions(){
  const profile = getActiveProfile();
  if(profile?.adapter !== 'openai-chat'){
    alert('当前 Agent 不是 OpenAI-compatible HTTP，不能读取 /v1/models。可以用“自定义”直接填写模型名。');
    return;
  }
  try{
    await refreshModelOptionsForProfile(profile.id);
    renderModelSwitcher();
  }catch(e){
    alert('读取模型失败：' + e.message);
  }
}
async function promptCustomModel(){
  const current = getActiveProfile()?.model || settings.model || '';
  const next = window.prompt('请输入模型名称：', current);
  if(next === null) return;
  await setActiveModel(next);
}
function renderAgentSwitcher(){
  const profile = getActiveProfile();
  const button = $('composerMode');
  const label = $('composerModeLabel');
  const menu = $('agentSwitchMenu');
  if(!button || !label || !menu) return;
  const suffix = agentModeSuffix(profile);
  label.textContent = profile ? `${profile.label}${suffix ? ` · ${suffix}` : ''}` : '未配置 Agent';
  button.title = profile ? `当前 Agent：${profile.label}。点击快速切换。` : '点击选择 Agent';
  const profiles = settings.agentProfiles || [];
  if(!profiles.length){
    menu.innerHTML = '<div class="agent-switch-empty">暂无可用 Agent，请到设置中添加。</div>';
    return;
  }
  menu.innerHTML = profiles.map(item => {
    const current = item.id === settings.activeAgentId;
    const suffix = agentModeSuffix(item);
    const endpoint = item.agentId ? `agent:${item.agentId}` : (item.model || item.baseUrl || item.binaryPath || '未设置模型');
    return `
      <button class="agent-switch-option ${current ? 'active' : ''}" data-id="${escapeHtml(item.id)}" role="option" aria-selected="${current}">
        <span class="agent-switch-dot ${item.type === 'openclaw' ? 'openclaw' : item.type === 'hermes' ? 'hermes' : item.type === 'claude' ? 'claude' : ''}"></span>
        <span class="agent-switch-copy">
          <strong>${escapeHtml(item.label)}</strong>
          <small>${escapeHtml(agentCapabilityLabel(item))} · ${escapeHtml(suffix || typeLabel(item.type))} · ${escapeHtml(endpoint)}</small>
        </span>
        ${current ? '<span class="agent-switch-check">✓</span>' : ''}
      </button>
    `;
  }).join('');
  menu.querySelectorAll('.agent-switch-option').forEach(btn => {
    btn.onclick = async (event) => {
      event.stopPropagation();
      const id = btn.dataset.id;
      closeAgentSwitcher();
      if(!id || id === settings.activeAgentId) return;
      await setActiveProfile(id);
    };
  });
}
function setSettingsTab(tab){
  settingsTab = tab || 'overview';
  document.querySelectorAll('[data-settings-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.settingsTab === settingsTab));
  document.querySelectorAll('[data-settings-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.settingsPanel === settingsTab));
}
function isAgentSwitcherOpen(){
  return !$('agentSwitchMenu')?.classList.contains('hidden');
}
function openAgentSwitcher(){
  renderAgentSwitcher();
  $('agentSwitchMenu')?.classList.remove('hidden');
  $('agentSwitcher')?.classList.add('open');
  $('composerMode')?.setAttribute('aria-expanded', 'true');
}
function closeAgentSwitcher(){
  $('agentSwitchMenu')?.classList.add('hidden');
  $('agentSwitcher')?.classList.remove('open');
  $('composerMode')?.setAttribute('aria-expanded', 'false');
}
function toggleAgentSwitcher(){
  if(isAgentSwitcherOpen()) closeAgentSwitcher();
  else openAgentSwitcher();
}
function openSettingsDialog(tab = 'overview'){
  settingsTab = tab || 'overview';
  const modal = $('settingsModal');
  modal.classList.remove('hidden');
  document.body.classList.add('settings-open');
  renderSettings();
  refreshContextPanel();
  window.setTimeout(() => $('closeSettings')?.focus(), 0);
}
function closeSettingsDialog(){
  $('settingsModal')?.classList.add('hidden');
  document.body.classList.remove('settings-open');
}
function renderAll(){ syncConnectionFromActiveProfile(); updateWorkspaceChrome(); renderSettings(); renderChatList(); renderMessages(); }
function renderTaskListDeps(){
  return {
    $,
    chats,
    activeId,
    settings,
    taskFilter,
    taskSort,
    currentAgentOnly,
    escapeHtml,
    getProfile,
    activeVisibleMessages,
    contentToText,
    save,
    renderAll,
    renderChatList,
    renderTaskFilters,
    setActiveId: (id) => { activeId = id; },
    setTaskFilter: (filter) => { taskFilter = filter || 'all'; },
  };
}
function matchesTaskFilter(chat, filter){ return AgentHubRenderTaskList.matchesTaskFilter(chat, filter); }
function renderTaskFilters(){ return AgentHubRenderTaskList.renderTaskFilters(renderTaskListDeps()); }
function formatRelativeTime(ts){ return AgentHubRenderTaskList.formatRelativeTime(ts); }
function chatStatusLabel(chat){ return AgentHubRenderTaskList.chatStatusLabel(chat); }
function chatStatusClass(chat){ return AgentHubRenderTaskList.chatStatusClass(chat); }
function chatPreview(chat){ return AgentHubRenderTaskList.chatPreview(chat, renderTaskListDeps()); }
function truncateForCard(text, limit = 72){ return AgentHubRenderTaskList.truncateForCard(text, limit); }
function renderTaskDetailDeps(){
  return {
    $,
    escapeHtml,
    getProfile,
    getActiveProfile,
    chatStatusClass,
    chatStatusLabel,
    formatRelativeTime,
    formatElapsed,
    truncateForCard,
    artifactPreviewText,
    agentRuns,
    handoffRecords,
    renameTask,
    deleteTask,
    toggleTaskPin,
    setTaskStatus,
    taskChromeCollapsed,
    taskChromeScale,
    taskChromePinned,
    setTaskChromeCollapsed,
    setTaskChromeScale,
    setTaskChromePinned,
    bindWorkspacePrompt,
    clearWorkspace,
    setApprovalPolicy,
    approvalPolicyLabel,
    workspaceLabel,
    contextStrategyLabel,
    taskRecommendation,
    taskRecommendationBusy,
    taskSpec,
    taskWorkflow,
    latestWorkflowAction,
    recommendAgentForTask,
    acceptTaskRecommendation,
    toggleTaskRecommendationDetail,
    generateTaskSpecForTask,
    transitionTaskStage,
    runWorkflowStageAction,
    sendWorkflowActionToRecommendedAgent,
    openRunDebugPanel,
    retryAgentRun,
    copyRunContext,
  };
}
function setTaskChromeCollapsed(value){
  taskChromeCollapsed = !!value;
  save();
  renderMessages();
}
function setTaskChromeScale(value){
  taskChromeScale = ['compact','normal','large'].includes(value) ? value : 'compact';
  save();
  renderMessages();
}
function setTaskChromePinned(value){
  taskChromePinned = !!value;
  save();
  renderMessages();
}
function renameTask(chat){
  const nextTitle = window.prompt('请输入新的会话名称：', chat.title || '新的任务');
  if(nextTitle === null) return;
  const value = nextTitle.trim();
  if(!value) return;
  chat.title = value.slice(0, 80);
  chat.updatedAt = Date.now();
  save();
  renderAll();
}
function deleteTask(chat){
  const ok = window.confirm(`确认删除会话「${chat.title || '新的任务'}」吗？

这会删除该任务的本地消息、附件资产、交接记录和运行记录。`);
  if(!ok) return;
  chats = chats.filter(item => item.id !== chat.id);
  if(!chats.length){
    createChat(false);
  } else if(activeId === chat.id){
    activeId = chats[0].id;
  }
  save();
  renderAll();
}
function toggleTaskPin(chat){
  chat.pinned = !chat.pinned;
  chat.updatedAt = Date.now();
  save();
  renderAll();
}
function setTaskStatus(chat, status){
  if(status === 'todo') chat.taskStatus = 'todo';
  if(status === 'active') chat.taskStatus = 'active';
  if(status === 'blocked') chat.taskStatus = 'blocked';
  if(status === 'handoff') chat.taskStatus = 'handoff';
  if(status === 'done') chat.taskStatus = 'done';
  chat.updatedAt = Date.now();
  save();
  renderAll();
}
async function recommendAgentForTask(chat = activeChat()){
  if(!chat || isFileMode) return;
  taskRecommendationBusy = true;
  taskRecommendation = taskRecommendation?.sessionId === chat.id ? taskRecommendation : null;
  renderTaskOverview();
  try{
    const latestUser = [...(chat.messages || [])].reverse().find(m => m.role === 'user');
    const d = await AgentHubApi.taskRouterRecommend({
      sessionId: chat.id,
      message: latestUser ? contentToText(latestUser.content) : '',
      workflowStage: taskWorkflow?.sessionId === chat.id ? taskWorkflow.currentStage : (taskSpec?.sessionId === chat.id ? taskSpec.stage : ''),
    });
    if(d.taskSpec) taskSpec = d.taskSpec;
    taskRecommendation = {
      ...(d.recommendation || {}),
      sessionId: chat.id,
      expanded: false,
    };
    chat.nextStep = `Agent Hub 已推荐 ${taskRecommendation.primaryAgentLabel || taskRecommendation.primaryAgentId || 'Agent'} 处理当前任务。`;
    chat.updatedAt = Date.now();
    save();
  }catch(e){
    alert('智能推荐失败：' + e.message);
  }finally{
    taskRecommendationBusy = false;
    renderAll();
  }
}
async function generateTaskSpecForTask(chat = activeChat()){
  if(!chat || isFileMode) return;
  try{
    if(serverSessionsReady) await syncSessionsToServer();
    const latestUser = [...(chat.messages || [])].reverse().find(m => m.role === 'user');
    const d = await AgentHubApi.generateTaskSpec({
      sessionId: chat.id,
      message: latestUser ? contentToText(latestUser.content) : '',
    });
    taskSpec = d.data || null;
    await refreshTaskWorkflow(chat.id);
    if(taskSpec?.goal && !chat.taskGoal) chat.taskGoal = taskSpec.goal;
    chat.nextStep = '已生成任务规格，可按验收标准推进执行。';
    chat.updatedAt = Date.now();
    save();
    renderAll();
  }catch(e){
    alert('生成任务规格失败：' + e.message);
  }
}
async function refreshTaskWorkflow(sessionId = activeId){
  if(isFileMode || !sessionId) return null;
  try{
    const d = await AgentHubApi.taskWorkflow(sessionId);
    taskWorkflow = d.data || null;
    return taskWorkflow;
  }catch(e){
    console.warn('workflow refresh skipped:', e.message);
    return null;
  }
}
async function transitionTaskStage(chat = activeChat(), stage){
  if(!chat || !stage || isFileMode) return;
  try{
    if(serverSessionsReady) await syncSessionsToServer();
    const d = await AgentHubApi.transitionTaskStage({sessionId: chat.id, stage, actor: 'user'});
    taskWorkflow = d.data || null;
    if(d.recommendation){
      taskRecommendation = {
        ...d.recommendation,
        sessionId: chat.id,
        expanded: false,
      };
    }
    const current = taskWorkflow?.current || {};
    chat.taskStatus = current.status || chat.taskStatus || 'active';
    chat.nextStep = d.recommendation?.primaryAgentLabel
      ? `已进入「${current.label || stage}」阶段，推荐由 ${d.recommendation.primaryAgentLabel} 继续处理。`
      : (current.nextStep || chat.nextStep || '');
    if(taskSpec) taskSpec = {...taskSpec, stage: taskWorkflow?.currentStage || stage};
    else {
      const spec = await AgentHubApi.taskSpec(chat.id);
      taskSpec = spec.data || null;
    }
    chat.updatedAt = Date.now();
    save();
    renderAll();
  }catch(e){
    alert('切换任务阶段失败：' + e.message);
  }
}
function currentWorkflowStageForChat(chat = activeChat()){
  if(!chat) return 'draft';
  if(taskWorkflow?.sessionId === chat.id && taskWorkflow.currentStage) return taskWorkflow.currentStage;
  if(taskSpec?.sessionId === chat.id && taskSpec.stage) return taskSpec.stage;
  if(chat.taskStatus === 'todo') return 'draft';
  if(chat.taskStatus === 'handoff') return 'review';
  if(chat.taskStatus === 'done' || chat.taskStatus === 'completed') return 'done';
  return 'executing';
}
function nextWorkflowStageAfterRun(stage, outcome){
  const current = String(stage || 'draft');
  if(outcome === 'success'){
    const next = {draft:'planning', planning:'executing', executing:'review', review:'testing', testing:'done'};
    return next[current] || '';
  }
  if(outcome === 'error' || outcome === 'cancelled'){
    if(['review','testing','done'].includes(current)) return 'executing';
    return current || 'executing';
  }
  return '';
}
async function autoLinkWorkflowAfterRun(chat = activeChat(), outcome = 'success'){
  if(!chat || isFileMode) return;
  if(!taskWorkflow && !taskSpec) return;
  const currentStage = currentWorkflowStageForChat(chat);
  const nextStage = nextWorkflowStageAfterRun(currentStage, outcome);
  if(!nextStage) return;
  const stageChanged = nextStage !== currentStage;
  const noteMap = {
    success: `Agent Run 已成功，Workflow 从「${currentStage}」联动到「${nextStage}」。`,
    error: `Agent Run 执行失败，Workflow 保持/回退到「${nextStage}」，等待修复后重试。`,
    cancelled: `Agent Run 已停止，Workflow 保持/回退到「${nextStage}」，等待调整后重试。`,
  };
  try{
    if(stageChanged || outcome !== 'success'){
      const d = await AgentHubApi.transitionTaskStage({
        sessionId: chat.id,
        stage: nextStage,
        actor: 'agent-run',
        note: noteMap[outcome] || ''
      });
      taskWorkflow = d.data || taskWorkflow;
      if(d.recommendation) taskRecommendation = {...d.recommendation, sessionId: chat.id, expanded: false};
      if(taskSpec) taskSpec = {...taskSpec, stage: taskWorkflow?.currentStage || nextStage};
    }
    if(outcome === 'success'){
      chat.taskStatus = taskWorkflow?.current?.status || chat.taskStatus || 'active';
      chat.nextStep = stageChanged
        ? `本轮 Agent Run 已完成，已自动进入「${taskWorkflow?.current?.label || nextStage}」阶段。`
        : '本轮 Agent Run 已完成，可继续推进下一步。';
    } else {
      chat.taskStatus = 'blocked';
      chat.nextStep = outcome === 'cancelled'
        ? '当前 Agent Run 已停止，Workflow 已关联到执行修复阶段；调整任务后可重试。'
        : '当前 Agent Run 失败，Workflow 已关联到执行修复阶段；请查看 Run Debug Panel 后重试。';
    }
    chat.updatedAt = Date.now();
    save();
    renderAll();
  }catch(e){
    console.warn('workflow auto link skipped:', e.message);
  }
}
async function runWorkflowStageAction(chat = activeChat(), stage = ''){
  if(!chat || isFileMode) return;
  const targetStage = stage || taskWorkflow?.currentStage || taskSpec?.stage || 'draft';
  try{
    if(serverSessionsReady) await syncSessionsToServer();
    const d = await AgentHubApi.workflowStageAction({sessionId: chat.id, stage: targetStage});
    const action = d.data || {};
    if(d.recommendation){
      taskRecommendation = {...d.recommendation, sessionId: chat.id, expanded: false};
    }
    latestWorkflowAction = {...action, sessionId: chat.id, recommendation: d.recommendation || null};
    chat.messages.push({
      id: uid(),
      role: 'assistant',
      content: action.content || '阶段动作已生成。',
      meta: `Agent Hub 阶段动作 · ${action.stageLabel || targetStage}`,
      createdAt: Date.now(),
      agentId: 'agent-hub-router'
    });
    chat.nextStep = `已生成「${action.title || '阶段动作'}」，可采用推荐 Agent 继续推进。`;
    chat.updatedAt = Date.now();
    save();
    renderAll();
  }catch(e){
    alert('生成阶段动作失败：' + e.message);
  }
}
async function sendWorkflowActionToRecommendedAgent(chat = activeChat()){
  if(!chat || busy || isFileMode) return;
  const action = latestWorkflowAction?.sessionId === chat.id ? latestWorkflowAction : null;
  if(!action?.content){
    alert('请先生成当前阶段动作，再发送给推荐 Agent。');
    return;
  }
  const rec = action.recommendation || taskRecommendation;
  if(rec){
    taskRecommendation = {...rec, sessionId: chat.id, expanded: false};
  }
  try{
    if(taskRecommendation?.primaryAgentId){
      await acceptTaskRecommendation(chat);
    }
    $('input').value = action.content;
    $('input').focus();
    await send();
  }catch(e){
    alert('发送给推荐 Agent 失败：' + e.message);
  }
}
async function acceptTaskRecommendation(chat = activeChat()){
  if(!chat || !taskRecommendation || taskRecommendation.sessionId !== chat.id) return;
  try{
    const d = await AgentHubApi.taskRouterAccept({
      sessionId: chat.id,
      recommendationId: taskRecommendation.id || '',
    });
    const rec = d.recommendation || taskRecommendation;
    const previousId = settings.activeAgentId;
    if(rec.primaryAgentId && rec.primaryAgentId !== previousId) await createAgentHandoff(previousId, rec.primaryAgentId);
    if(rec.primaryAgentId) settings.activeAgentId = rec.primaryAgentId;
    if(rec.contextStrategy) chat.contextStrategy = rec.contextStrategy;
    chat.activeAgentId = settings.activeAgentId;
    chat.taskStatus = 'planned';
    chat.nextStep = `已采用 Agent Hub 推荐：${getProfile(settings.activeAgentId)?.label || settings.activeAgentId}，可继续发送任务执行。`;
    chat.updatedAt = Date.now();
    taskRecommendation = {...rec, sessionId: chat.id, accepted: true, expanded: false};
    syncConnectionFromActiveProfile();
    syncSystemPrompts();
    save();
    renderAll();
    await health();
  }catch(e){
    alert('采用推荐失败：' + e.message);
  }
}
function toggleTaskRecommendationDetail(){
  if(!taskRecommendation) return;
  taskRecommendation = {...taskRecommendation, expanded: !taskRecommendation.expanded};
  renderTaskOverview();
}
function renderTaskOverview(){ return AgentHubRenderTaskDetail.renderTaskOverview(activeChat(), renderTaskDetailDeps()); }
function renderTaskDetailPanels(){ return AgentHubRenderTaskDetail.renderTaskDetailPanels(activeChat(), renderTaskDetailDeps()); }
function sortChats(list){ return AgentHubRenderTaskList.sortChats(list, taskSort); }
function renderTaskToolbar(){ return AgentHubRenderTaskList.renderTaskToolbar(renderTaskListDeps()); }
function renderChatList(){ return AgentHubRenderTaskList.renderChatList(renderTaskListDeps()); }
function contextDeps(){
  return {
    activeAgentName,
    options: {
      maxContextMessages: MAX_CONTEXT_MESSAGES,
      maxContextChars: MAX_CONTEXT_CHARS,
      maxSingleMessageChars: MAX_SINGLE_MESSAGE_CHARS,
      systemContentMaxChars: 4200,
    }
  };
}
function contentToText(content){ return AgentHubContext.contentToText(content); }
function trimMessageContent(content, maxChars = MAX_SINGLE_MESSAGE_CHARS){ return AgentHubContext.trimMessageContent(content, maxChars); }
function artifactPreviewText(artifact){ return AgentHubContext.artifactPreviewText(artifact); }
function artifactContextBlock(chat){ return AgentHubContext.artifactContextBlock(chat); }
function messagesForApi(chat){ return AgentHubContext.messagesForApi(chat, contextDeps()); }
function summarizeApiContext(messages){ return AgentHubContext.summarizeApiContext(messages); }
function currentContextStats(){ return AgentHubContext.currentContextStats(activeChat(), contextDeps()); }
function renderChatDeps(){
  return {
    $,
    escapeHtml,
    getProfile,
    getActiveProfile,
    activeAgentName,
    renderTaskOverview,
    renderTaskDetailPanels,
  };
}
function roleLabel(message){ return AgentHubRenderChat.roleLabel(message, renderChatDeps()); }
function displayContent(content){ return AgentHubRenderChat.displayContent(content); }
function formatElapsed(ms){ return AgentHubRenderChat.formatElapsed(ms); }
function activeVisibleMessages(chat){ return AgentHubRenderChat.activeVisibleMessages(chat); }
function renderHandoffBanner(){ return AgentHubRenderChat.renderHandoffBanner(activeChat(), renderChatDeps()); }
function renderMessages(){ return AgentHubRenderChat.renderMessages(activeChat(), renderChatDeps()); }
function setBadge(type, text){ return AgentHubRenderChat.setBadge(type, text, renderChatDeps()); }
function setBusy(v){ busy = v; return AgentHubRenderChat.setBusy(v, renderChatDeps()); }
function showBootNotice(show){ return AgentHubRenderChat.showBootNotice(show, renderChatDeps()); }
function updateRunStatus(progress){ return AgentHubRenderChat.updateRunStatus(progress, renderChatDeps()); }
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
function completeProgress(chat, progress, text, isError, options = {}){
  stopRunTicker(); updateRunStatus(null); removeProgressMessage(chat, progress.id);
  const elapsed = Date.now() - progress.startedAt;
  const successMeta = progress.finalMeta || `${activeAgentName()} · ${activeModelLabel()}`;
  const cancelled = !!options.cancelled;
  chat.messages.push({ id: uid(), role: cancelled ? 'assistant' : (isError ? 'error' : 'assistant'), content: text, meta: `${formatElapsed(elapsed)} · ${cancelled ? '已停止' : isError ? '执行失败' : successMeta}`, createdAt: Date.now(), agentId: settings.activeAgentId });
  if(cancelled){
    chat.taskStatus = 'blocked';
    chat.nextStep = '当前任务已停止。可以调整任务描述后重新发送，或切换 Agent 接力。';
  } else if(isError){
    chat.taskStatus = 'blocked';
    chat.nextStep = `检查 ${activeAgentName()} 连接状态或调整任务描述后重试。`;
  } else {
    chat.taskStatus = 'active';
    chat.nextStep = `可继续让 ${activeAgentName()} 深入下一步，或切换 Agent 执行落地动作。`;
  }
  chat.updatedAt = Date.now();
  window.setTimeout(() => autoLinkWorkflowAfterRun(chat, cancelled ? 'cancelled' : isError ? 'error' : 'success'), 0);
}
function beginAssistantStream(chat, progress){
  stopRunTicker(); updateRunStatus(null); removeProgressMessage(chat, progress.id);
  const msg = { id: uid(), role: 'assistant', content: '', meta: `正在流式接收 · ${activeAgentName()} · ${activeModelLabel()}`, createdAt: Date.now(), agentId: settings.activeAgentId };
  chat.messages.push(msg);
  return msg;
}
function appendAssistantDelta(msg, delta){ msg.content += delta; msg.meta = `正在流式接收 · ${activeAgentName()} · ${activeModelLabel()}`; renderMessages(); }
function finishAssistantStream(msg, started){
  msg.meta = `${formatElapsed(Date.now() - started)} · ${activeAgentName()} · ${activeModelLabel()}`;
  const chat = activeChat();
  if(chat){
    chat.taskStatus = 'active';
    chat.nextStep = `可继续让 ${activeAgentName()} 深入当前任务，或切换 Agent 执行下一步。`;
    chat.updatedAt = Date.now();
    window.setTimeout(() => autoLinkWorkflowAfterRun(chat, 'success'), 0);
  }
}
function extractDeltaFromChunk(data){
  if(!data || data === '[DONE]') return '';
  const parsed = JSON.parse(data);
  if(parsed.choices?.[0]?.delta?.content) return parsed.choices[0].delta.content;
  if(parsed.choices?.[0]?.message?.content) return parsed.choices[0].message.content;
  if(parsed.output_text) return parsed.output_text;
  if(parsed.response?.output_text) return parsed.response.output_text;
  return '';
}
function isTimeoutError(error){
  return /timed out|timeout|超时/i.test(String(error?.message || error || ''));
}
async function streamChat(payload, chat, progress, started){
  activeAbortController = new AbortController();
  const r = await AgentHubApi.chatStream(payload, {signal: activeAbortController.signal});
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
      if(eventName === 'cancelled') {
        const info = JSON.parse(data);
        completeProgress(chat, progress, info.error || '任务已停止。', false, {cancelled:true});
        setBadge('unknown','任务已停止');
        return;
      }
      if(eventName === 'progress'){
        const info = JSON.parse(data);
        progress.statusText = info.statusText || progress.statusText || 'Agent 正在执行中';
        progress.meta = `${formatElapsed(info.latencyMs ?? Date.now() - progress.startedAt)} · ${progress.statusText}`;
        updateRunStatus(progress);
        renderMessages();
        continue;
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
  chat.updatedAt = Date.now();
  setBadge('ok', activeAgentName() + ' 在线');
}
async function nonStreamChat(payload, chat, progress, started){
  const d = await AgentHubApi.chat(payload);
  let reply = d.choices?.[0]?.message?.content ?? JSON.stringify(d,null,2);
  if(Array.isArray(reply)) reply = reply.map(p=>p.text||JSON.stringify(p)).join('\n');
  markStep(progress, 3, 'done');
  progress.statusText = 'Agent 已返回结果，正在整理展示';
  const detail = d._ui?.adapter === 'openclaw-cli'
    ? `OpenClaw CLI · agent:${d._ui?.agentId || 'main'} · session:${d._ui?.sessionId || payload.session_id || 'default'}`
    : d._ui?.adapter === 'openclaw-gateway-rpc'
    ? `OpenClaw Gateway RPC · ${d._ui?.rpcReused ? '复用连接' : '新建连接'} · agent:${d._ui?.agentId || 'main'} · session:${d._ui?.sessionId || payload.session_id || 'default'}`
    : d._ui?.adapter === 'claude-code-cli'
    ? `Claude Code CLI · session:${d._ui?.sessionId || payload.session_id || 'default'}`
    : '完成';
  progress.meta = `${formatElapsed(d._ui?.latencyMs ?? Date.now()-started)} · ${detail}`;
  progress.finalMeta = detail;
  completeProgress(chat, progress, reply, false);
  setBadge('ok', activeAgentName() + ' 在线');
}
function autoTitle(c, text){ if(c.title.startsWith('新的会话')) c.title = text.slice(0,28) || '附件分析'; }
async function cancelActiveRun(){
  if(!activeRun?.chatId || !activeRun?.progressId) return;
  const chat = chats.find(item => item.id === activeRun.chatId);
  const progress = chat?.messages.find(m => m.id === activeRun.progressId);
  activeAbortController?.abort();
  try{
    await AgentHubApi.cancelRun({
      sessionId: activeRun.chatId,
      runId: activeRun.runId || activeRun.progressId,
      agentId: settings.activeAgentId,
      adapter: getActiveProfile()?.adapter || '',
    });
  }catch(e){
    console.warn('Agent Hub cancel signal failed:', e.message);
  }
  if(chat && progress){
    progress.statusText = '用户已停止当前任务';
    completeProgress(chat, progress, '已停止当前任务。注意：部分本地 Agent 可能仍会在后台完成原始调用，但 Agent Hub 已停止等待并会忽略后续结果。', false, {cancelled:true});
  }
  activeRun = null;
  activeProgressId = null;
  activeAbortController = null;
  setBusy(false);
  save();
  renderAll();
}
async function readConfig(){
  if(isFileMode){ showBootNotice(true); return; }
  try{
    const d = await AgentHubApi.config();
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
    await loadSessionsFromServer();
    syncSystemPrompts();
    save();
  }catch{}
}
async function discoverAgents(){
  if(isFileMode) return;
  settingsTab = 'discovery';
  const out = $('settingsOutput');
  const btn = $('discoverBtn');
  btn.disabled = true;
  btn.textContent = '扫描中…';
  out.textContent = 'Agent Hub 正在扫描本机 Hermes / OpenClaw…';
  $('discoverList').innerHTML = '<div class="discovery-empty">正在扫描本机配置、命令行工具和本地端口…</div>';
  try{
    const d = await AgentHubApi.discoverAgents();
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
  return AgentHubAgents.adapterLabel(adapter);
}
function typeLabel(type){ return AgentHubAgents.typeLabel(type); }
function agentCapabilityLabel(profile){
  return AgentHubAgents.agentCapabilityLabel(profile);
}
function agentCapabilityCard(profile){
  return AgentHubAgents.agentCapabilityCard(profile);
}
function sourceLabel(source){ return AgentHubAgents.sourceLabel(source); }
function profileStatusBadge(profile){
  if(profile.adapter === 'openclaw-gateway-rpc') return '<span class="pill ok">RPC</span>';
  if(profile.adapter === 'openclaw-gateway') return '<span class="pill warm">Gateway</span>';
  if(profile.adapter === 'openclaw-cli') return '<span class="pill warm">CLI</span>';
  if(profile.adapter === 'claude-code-cli') return '<span class="pill ok">Claude CLI</span>';
  if(profile.status === 'online' || profile.reachable === true) return '<span class="pill ok">在线</span>';
  if(profile.status) return `<span class="pill">${escapeHtml(profile.status)}</span>`;
  return '';
}
function profileCapabilityHtml(profile, compact = false){
  const card = agentCapabilityCard(profile);
  const badgeHtml = (card.badges || []).slice(0, compact ? 3 : 5).map(item => `<span>${escapeHtml(item)}</span>`).join('');
  const strengths = (card.strengths || []).slice(0, compact ? 2 : 3).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  const cautions = (card.cautions || []).slice(0, compact ? 1 : 3).map(item => `<li>${escapeHtml(item)}</li>`).join('');
  return `
    <div class="capability-card capability-${escapeHtml(card.risk || 'unknown')} ${compact ? 'compact' : ''}">
      <div class="capability-card-head">
        <div>
          <strong>${escapeHtml(card.role || card.label)}</strong>
          <p>${escapeHtml(card.bestFor || '')}</p>
        </div>
        <span class="capability-risk">${escapeHtml(card.riskLabel || '')}</span>
      </div>
      <div class="capability-badges">${badgeHtml}</div>
      ${compact ? '' : `
        <div class="capability-cols">
          <div><small>优势</small><ul>${strengths}</ul></div>
          <div><small>注意</small><ul>${cautions}</ul></div>
        </div>`}
    </div>`;
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
  renderModelRegistryPanel();
  renderDiscoverList();
  renderWorkspacePanel();
  renderContextStrategyControl();
  renderContextPanel();
  renderContextPackagePanel();
  renderProjectMemoryPanel();
  fillProfileForm(editing);
  $('deleteProfileBtn').disabled = settings.agentProfiles.length <= 1;
  setSettingsTab(settingsTab);
}
function renderContextPanel(){
  const root = $('contextPanel');
  if(!root) return;
  const chat = activeChat();
  if(!chat){
    root.innerHTML = '<div class="context-empty">暂无当前会话。</div>';
    return;
  }
  const stats = currentContextStats();
  const runs = agentRuns.filter(run => run.session_id === chat.id).slice(0, 6);
  root.innerHTML = `
    <div class="context-grid">
      <div class="context-stat"><strong>${escapeHtml(chat.title || '新的会话')}</strong><span>当前任务</span></div>
      <div class="context-stat"><strong>${stats.conversational}</strong><span>发送上下文消息</span></div>
      <div class="context-stat"><strong>~${stats.chars}</strong><span>上下文字符</span></div>
      <div class="context-stat"><strong>${stats.hasHandoff ? '已生成' : '暂无'}</strong><span>Agent 交接</span></div>
      <div class="context-stat"><strong>${escapeHtml(chatStatusLabel(chat))}</strong><span>任务状态</span></div>
      <div class="context-stat"><strong>${stats.artifacts}</strong><span>附件资产</span></div>
      <div class="context-stat"><strong>${stats.pinnedArtifacts}</strong><span>固定上下文资产</span></div>
    </div>
    <div class="context-block">
      <div class="context-label">任务目标</div>
      <p>${escapeHtml(chat.taskGoal || '暂无明确任务目标；首条用户消息会自动提炼。')}</p>
    </div>
    <div class="context-block">
      <div class="context-label">下一步</div>
      <p>${escapeHtml(chat.nextStep || '暂无下一步建议。')}</p>
    </div>
    <div class="context-block">
      <div class="context-label">会话摘要</div>
      <p>${escapeHtml(chat.summary || '暂无摘要；发送消息或切换 Agent 后会自动生成。')}</p>
    </div>
    <div class="context-block">
      <div class="context-label">最近交接</div>
      <p>${escapeHtml(chat.handoffSummary || '暂无交接摘要。')}</p>
      ${chat.handoffSummary ? `
        <div class="context-toolbar">
          <button data-action="show-handoff">在主界面展开</button>
          <button data-action="clear-handoff">清空交接摘要</button>
        </div>` : ''}
    </div>
    <div class="context-block">
      <div class="context-label">最近附件资产</div>
      ${chat.artifacts?.length ? chat.artifacts.slice(-6).reverse().map(artifact => `
        <div class="artifact-item">
          <div class="artifact-item-head">
            <span class="run-status ${artifact.includeInContext !== false ? 'ok' : 'warm'}">${escapeHtml(artifact.includeInContext !== false ? '已固定到上下文' : '未固定')}</span>
            <strong>${escapeHtml(artifact.name || 'attachment')}</strong>
            <span class="artifact-meta">${escapeHtml(artifact.mime || artifact.kind || '-')}</span>
          </div>
          <p class="artifact-preview">${escapeHtml(artifactPreviewText(artifact))}</p>
          <div class="context-toolbar">
            <button data-action="toggle-artifact" data-id="${escapeHtml(artifact.id)}" class="${artifact.includeInContext !== false ? 'active' : ''}">${artifact.includeInContext !== false ? '取消固定' : '固定到上下文'}</button>
            <button data-action="remove-artifact" data-id="${escapeHtml(artifact.id)}">移除资产</button>
          </div>
        </div>
      `).join('') : '<p>暂无附件资产。</p>'}
    </div>
    <div class="context-block">
      <div class="context-label">Agent Run 历史</div>
      ${runs.length ? runs.map(run => `
        <div class="run-row">
          <span class="run-status ${run.status === 'success' ? 'ok' : 'bad'}">${escapeHtml(run.status)}</span>
          <span>${escapeHtml(run.agent_id || run.adapter || 'agent')}</span>
          <span>${formatElapsed(run.latency_ms || 0)}</span>
          <small>${escapeHtml(run.output_summary || run.error || run.input_summary || '')}</small>
        </div>
      `).join('') : '<p>暂无执行记录。</p>'}
    </div>
  `;
  root.querySelectorAll('button[data-action]').forEach(btn => {
    btn.onclick = () => {
      const action = btn.dataset.action;
      if(action === 'show-handoff'){
        chat.handoffDismissed = false;
        save();
        renderHandoffBanner();
        return;
      }
      if(action === 'clear-handoff'){
        chat.handoffSummary = '';
        chat.handoffFromAgentId = '';
        chat.handoffFromLabel = '';
        chat.handoffToAgentId = '';
        chat.handoffToLabel = '';
        chat.handoffAt = 0;
        chat.handoffDismissed = false;
        save();
        renderContextPanel();
        renderHandoffBanner();
        return;
      }
      if(action === 'toggle-artifact'){
        const item = (chat.artifacts || []).find(entry => entry.id === btn.dataset.id);
        if(!item) return;
        item.includeInContext = item.includeInContext === false;
        chat.updatedAt = Date.now();
        save();
        renderContextPanel();
        return;
      }
      if(action === 'remove-artifact'){
        chat.artifacts = (chat.artifacts || []).filter(entry => entry.id !== btn.dataset.id);
        chat.updatedAt = Date.now();
        save();
        renderContextPanel();
      }
    };
  });
}
function workspaceLabel(chat){
  if(!chat?.workspacePath) return '未绑定项目';
  const name = chat.workspaceName || chat.workspacePath.split('/').filter(Boolean).pop() || chat.workspacePath;
  const git = chat.workspaceGitRepo ? ` · ${chat.workspaceGitBranch || 'git'}${chat.workspaceGitDirty ? ' · 有变更' : ' · clean'}` : '';
  return `${name}${git}`;
}
function approvalPolicyLabel(value){
  return value === 'readonly' ? '只读' : value === 'workspace-auto' ? '工作区自动' : value === 'bypass' ? '跳过确认' : '自动判断';
}
function setApprovalPolicy(value){
  const chat = activeChat();
  if(!chat) return;
  chat.approvalPolicy = value || 'auto';
  chat.updatedAt = Date.now();
  save();
  renderAll();
}
function renderWorkspacePanel(){
  const chat = activeChat();
  const input = $('workspacePathInput');
  const status = $('workspaceStatus');
  if(!input || !status) return;
  input.value = chat?.workspacePath || '';
  if(!chat?.workspacePath){
    status.className = 'workspace-status';
    status.textContent = '当前任务未绑定项目路径。绑定后会写入 Context Package，并提示本地 Agent 在该目录下继续任务。';
    return;
  }
  status.className = `workspace-status ${chat.workspaceGitRepo ? 'ok' : 'warm'}`;
  status.textContent = `${workspaceLabel(chat)} · ${chat.workspacePath}${chat.workspaceCheckedAt ? ` · ${formatRelativeTime(chat.workspaceCheckedAt)}检查` : ''}`;
}
function contextStrategyLabel(value){
  return value === 'light' ? '轻量' : value === 'full' ? '完整' : value === 'file' ? '文件任务' : value === 'code' ? '代码任务' : '标准';
}
function contextStrategyHint(value){
  return {
    light: '轻量：只保留目标、摘要、少量最近对话，适合简单问答。',
    standard: '标准：摘要、交接、文件、最近记录和项目记忆，默认推荐。',
    full: '完整：尽量保留更多 timeline、记忆和附件，适合复杂接力。',
    file: '文件任务：优先保留文件路径、附件、输出文件和文档处理记录。',
    code: '代码任务：优先保留项目工作区、命令、执行记录和项目记忆。',
  }[value || 'standard'] || '标准：摘要、交接、文件、最近记录和项目记忆，默认推荐。';
}
function renderContextStrategyControl(){
  const chat = activeChat();
  const select = $('contextStrategySelect');
  const hint = $('contextStrategyHint');
  if(!select || !hint || !chat) return;
  select.value = chat.contextStrategy || 'standard';
  hint.textContent = contextStrategyHint(select.value);
}
function setContextStrategy(value){
  const chat = activeChat();
  if(!chat) return;
  chat.contextStrategy = value || 'standard';
  chat.updatedAt = Date.now();
  contextPackage = null;
  save();
  renderContextStrategyControl();
  renderContextPackagePanel();
  renderTaskOverview();
}
async function bindWorkspacePath(path){
  const chat = activeChat();
  if(!chat) return;
  const info = (await AgentHubApi.inspectWorkspace(path)).workspace;
  if(!info?.exists || !info?.isDir) throw new Error(info?.error || '项目路径不可用');
  chat.workspacePath = info.path || path;
  chat.workspaceName = info.name || '';
  chat.workspaceGitRepo = !!info.isGitRepo;
  chat.workspaceGitBranch = info.gitBranch || '';
  chat.workspaceGitDirty = !!info.gitDirty;
  chat.workspaceCheckedAt = info.checkedAt || Date.now();
  chat.updatedAt = Date.now();
  save();
  renderAll();
  renderWorkspacePanel();
  refreshProjectMemories().catch(() => {});
  contextPackage = null;
  renderContextPackagePanel();
}
async function inspectAndBindWorkspace(){
  const input = $('workspacePathInput');
  const status = $('workspaceStatus');
  const path = input?.value.trim() || '';
  if(!path){ status.textContent = '请输入项目目录路径。'; return; }
  status.textContent = '正在检查项目路径和 Git 状态…';
  try{
    await bindWorkspacePath(path);
  }catch(e){
    status.className = 'workspace-status bad';
    status.textContent = '绑定失败：' + e.message;
  }
}
function clearWorkspace(){
  const chat = activeChat();
  if(!chat) return;
  chat.workspacePath = '';
  chat.workspaceName = '';
  chat.workspaceGitRepo = false;
  chat.workspaceGitBranch = '';
  chat.workspaceGitDirty = false;
  chat.workspaceCheckedAt = 0;
  chat.updatedAt = Date.now();
  contextPackage = null;
  save();
  renderAll();
  renderWorkspacePanel();
  projectMemories = [];
  renderProjectMemoryPanel();
  renderContextPackagePanel();
}
async function bindWorkspacePrompt(chat = activeChat()){
  if(!chat) return;
  const path = prompt('请输入当前任务绑定的本地项目目录：', chat.workspacePath || '');
  if(path === null) return;
  try{
    await bindWorkspacePath(path);
  }catch(e){
    alert('项目路径绑定失败：' + e.message);
  }
}
async function refreshContextPanel(){
  settingsTab = 'overview';
  if(isFileMode) { renderContextPanel(); return; }
  try{
    if(serverSessionsReady) await syncSessionsToServer();
    const chat = activeChat();
    if(chat){
      const r = await fetch(`/api/runs?session_id=${encodeURIComponent(chat.id)}&limit=20`);
      const d = await r.json();
      if(r.ok && d.ok !== false) agentRuns = d.data || [];
      const h = await AgentHubApi.handoffs(chat.id, 20);
      handoffRecords = h.data || [];
      const spec = await AgentHubApi.taskSpec(chat.id);
      taskSpec = spec.data || null;
      await refreshTaskWorkflow(chat.id);
    }
  }catch(e){
    console.warn('refresh context skipped:', e.message);
  }
  renderContextPanel();
  renderContextPackagePanel();
}
function renderContextPackagePanel(){
  const root = $('contextPackagePanel');
  if(!root) return;
  const chat = activeChat();
  if(!chat){ root.textContent = '暂无当前会话。'; return; }
  if(!contextPackage || contextPackage.sessionId !== chat.id){
    root.innerHTML = '<div class="context-empty">暂无上下文包。点击“生成上下文包”后，可查看 Agent Hub 注入给本地 Agent 的标准任务上下文。</div>';
    return;
  }
  const pkg = contextPackage.package || {};
  const stats = pkg.stats || {};
  const task = pkg.task || {};
  root.innerHTML = `
    <div class="context-package-card">
      <div class="context-package-head">
        <div>
          <strong>${escapeHtml(pkg.schema || 'agent-hub.context-package.v1')}</strong>
          <p>${escapeHtml(pkg.title || chat.title || '当前任务')} · ${escapeHtml(pkg.targetAgentLabel || pkg.targetAgentId || activeAgentName())}</p>
        </div>
        <span>${escapeHtml(stats.chars ? `~${stats.chars} chars` : 'ready')}</span>
      </div>
      <div class="context-package-stats">
        <span>消息 ${escapeHtml(stats.recentMessages ?? 0)}/${escapeHtml(stats.messages ?? 0)}</span>
        <span>附件 ${escapeHtml(stats.pinnedArtifacts ?? 0)}/${escapeHtml(stats.artifacts ?? 0)}</span>
        <span>Runs ${escapeHtml(stats.runs ?? 0)}</span>
        <span>Handoffs ${escapeHtml(stats.handoffs ?? 0)}</span>
        <span>记忆 ${escapeHtml(stats.memories ?? 0)}</span>
        <span>策略 ${escapeHtml(contextStrategyLabel(pkg.strategy || chat.contextStrategy || 'standard'))}</span>
      </div>
      <div class="context-package-goal"><b>目标</b>${escapeHtml(task.goal || '未设置')}</div>
      <pre>${escapeHtml(contextPackage.text || '')}</pre>
    </div>`;
}
function runDebugText(run){
  const debug = run?.debug || {};
  return [
    `Run ID: ${run?.id || ''}`,
    `Session: ${run?.session_id || ''}`,
    `Agent: ${debug.agentLabel || run?.agent_id || ''}`,
    `Adapter: ${debug.adapter || run?.adapter || ''}`,
    `Model: ${debug.model || ''}`,
    `Status: ${run?.status || ''}`,
    `Latency: ${formatElapsed(run?.latency_ms || 0)}`,
    `Workspace: ${debug.workspacePath || ''}`,
    `Approval: ${debug.approvalPolicy || ''}`,
    '',
    '## Messages',
    JSON.stringify(debug.messages || [], null, 2),
    '',
    '## Context Package',
    debug.contextPackageText || '',
    '',
    '## Output',
    run?.output_summary || '',
    '',
    '## Error',
    run?.error || debug.errorDetail || '',
  ].join('\n');
}
function openRunDebugPanel(run){
  const modal = $('runDebugModal');
  const root = $('runDebugContent');
  if(!modal || !root || !run) return;
  const debug = run.debug || {};
  $('runDebugTitle').textContent = `${debug.agentLabel || run.agent_id || 'Agent'} · ${runStatusLabelLocal(run.status)}`;
  $('runDebugMeta').textContent = `${debug.adapter || run.adapter || 'adapter'} · ${debug.model || 'model'} · ${formatElapsed(run.latency_ms || 0)} · ${run.created_at ? new Date(run.created_at).toLocaleString('zh-CN') : ''}`;
  root.innerHTML = `
    <div class="run-debug-toolbar">
      <button id="copyRunDebugBtn">复制调试信息</button>
      <button id="retryRunDebugBtn" class="primary">用当前上下文重试</button>
    </div>
    <div class="run-debug-grid">
      <div><span>状态</span><strong>${escapeHtml(run.status || '-')}</strong></div>
      <div><span>耗时</span><strong>${escapeHtml(formatElapsed(run.latency_ms || 0))}</strong></div>
      <div><span>Agent</span><strong>${escapeHtml(debug.agentLabel || run.agent_id || '-')}</strong></div>
      <div><span>模型</span><strong>${escapeHtml(debug.model || '-')}</strong></div>
      <div><span>工作区</span><strong>${escapeHtml(debug.workspacePath || '-')}</strong></div>
      <div><span>权限</span><strong>${escapeHtml(debug.approvalPolicy || '-')}</strong></div>
    </div>
    <div class="run-debug-section">
      <h3>发送给 Agent 的 Messages</h3>
      <pre>${escapeHtml(JSON.stringify(debug.messages || [], null, 2))}</pre>
    </div>
    <div class="run-debug-section">
      <h3>Context Package Snapshot</h3>
      <pre>${escapeHtml(debug.contextPackageText || '暂无 Context Package 快照。')}</pre>
    </div>
    <div class="run-debug-section">
      <h3>输出 / 错误</h3>
      <pre>${escapeHtml(run.output_summary || run.error || debug.errorDetail || '暂无输出。')}</pre>
    </div>
  `;
  $('copyRunDebugBtn').onclick = () => copyRunContext(run);
  $('retryRunDebugBtn').onclick = () => retryAgentRun(run);
  modal.classList.remove('hidden');
}
function closeRunDebugPanel(){
  $('runDebugModal')?.classList.add('hidden');
}
function runStatusLabelLocal(status){
  return status === 'success' ? '完成' : status === 'error' ? '失败' : status === 'cancelled' ? '已取消' : status || '记录';
}
async function copyRunContext(run){
  const text = runDebugText(run);
  try{
    await navigator.clipboard.writeText(text);
    setBadge('ok', '已复制 Run 调试信息');
  }catch{
    window.prompt('复制以下调试信息：', text);
  }
}
function retryAgentRun(run){
  const chat = activeChat();
  if(!chat) return;
  const debug = run?.debug || {};
  if(debug.agentProfileId && getProfile(debug.agentProfileId)){
    settings.activeAgentId = debug.agentProfileId;
    chat.activeAgentId = debug.agentProfileId;
  }
  if(debug.model && getActiveProfile()) getActiveProfile().model = debug.model;
  if(debug.contextStrategy) chat.contextStrategy = debug.contextStrategy;
  const lastUser = [...(chat.messages || [])].reverse().find(m => m.role === 'user');
  $('input').value = lastUser ? contentToText(lastUser.content) : (debug.messages || []).filter(m => m.role === 'user').slice(-1)[0]?.text || '';
  chat.nextStep = '已载入上次执行的 Agent / 模型 / 上下文策略，可确认后重新发送。';
  chat.updatedAt = Date.now();
  closeRunDebugPanel();
  save();
  renderAll();
  $('input').focus();
}
async function refreshContextPackage(){
  const chat = activeChat();
  if(!chat) return;
  const root = $('contextPackagePanel');
  if(root) root.innerHTML = '<div class="context-empty">正在生成标准上下文包…</div>';
  try{
    if(serverSessionsReady) await syncSessionsToServer();
    const d = await AgentHubApi.contextPackage(chat.id, settings.activeAgentId, chat.contextStrategy || 'standard');
    contextPackage = {sessionId: chat.id, package: d.package, text: d.text};
  }catch(e){
    contextPackage = null;
    if(root) root.innerHTML = `<div class="context-empty">上下文包生成失败：${escapeHtml(e.message)}</div>`;
    return;
  }
  renderContextPackagePanel();
}

function renderProjectMemoryPanel(){
  const root = $('projectMemoryPanel');
  if(!root) return;
  const chat = activeChat();
  if(!chat?.workspacePath){
    root.innerHTML = '<div class="context-empty">当前任务未绑定项目工作区。绑定后可沉淀项目规则、常用命令、文件路径和 Agent 执行经验。</div>';
    return;
  }
  if(!projectMemories.length){
    root.innerHTML = `
      <div class="context-empty">当前项目还没有记忆。点击“从当前任务提取”，Agent Hub 会把任务摘要、文件路径、命令和执行经验沉淀为项目记忆。</div>
      <div class="memory-form">
        <input id="memoryTitleInput" placeholder="手动添加记忆标题，例如：测试命令" />
        <textarea id="memoryContentInput" placeholder="记忆内容，例如：修改后运行 python3 -m unittest discover -s tests && node tests/smoke_frontend.js"></textarea>
        <button id="saveMemoryBtn">保存记忆</button>
      </div>`;
  } else {
    root.innerHTML = `
      <div class="memory-list">
        ${projectMemories.map(item => `
          <article class="memory-card">
            <div class="memory-card-head">
              <span>${escapeHtml(item.kind || 'note')}</span>
              <strong>${escapeHtml(item.title || '项目记忆')}</strong>
              <button data-memory-delete="${escapeHtml(item.id)}">删除</button>
            </div>
            <p>${escapeHtml(item.content || '')}</p>
          </article>
        `).join('')}
      </div>
      <div class="memory-form">
        <input id="memoryTitleInput" placeholder="手动添加记忆标题，例如：测试命令" />
        <textarea id="memoryContentInput" placeholder="记忆内容，例如：修改后运行 python3 -m unittest discover -s tests && node tests/smoke_frontend.js"></textarea>
        <button id="saveMemoryBtn">保存记忆</button>
      </div>`;
  }
  root.querySelectorAll('[data-memory-delete]').forEach(btn => {
    btn.onclick = async () => {
      await AgentHubApi.deleteMemory(btn.dataset.memoryDelete);
      await refreshProjectMemories();
    };
  });
  const saveBtn = root.querySelector('#saveMemoryBtn');
  if(saveBtn) saveBtn.onclick = saveManualMemory;
}
async function refreshProjectMemories(){
  const chat = activeChat();
  if(!chat?.workspacePath){ projectMemories = []; renderProjectMemoryPanel(); return; }
  try{
    const d = await AgentHubApi.memories(chat.workspacePath, '', 30);
    projectMemories = d.data || [];
  }catch(e){
    projectMemories = [];
    const root = $('projectMemoryPanel');
    if(root) root.innerHTML = `<div class="context-empty">项目记忆读取失败：${escapeHtml(e.message)}</div>`;
    return;
  }
  renderProjectMemoryPanel();
}
async function extractMemoriesFromCurrentTask(){
  const chat = activeChat();
  if(!chat?.workspacePath){ alert('请先绑定项目工作区。'); return; }
  if(serverSessionsReady) await syncSessionsToServer();
  await AgentHubApi.extractMemories({session_id: chat.id, workspace_path: chat.workspacePath});
  await refreshProjectMemories();
  contextPackage = null;
  renderContextPackagePanel();
}
async function saveManualMemory(){
  const chat = activeChat();
  if(!chat?.workspacePath){ alert('请先绑定项目工作区。'); return; }
  const title = $('memoryTitleInput')?.value.trim() || '项目记忆';
  const content = $('memoryContentInput')?.value.trim() || '';
  if(!content){ alert('请填写记忆内容。'); return; }
  await AgentHubApi.saveMemory({workspacePath: chat.workspacePath, kind:'manual', title, content, sourceSessionId: chat.id, confidence:0.9});
  await refreshProjectMemories();
  contextPackage = null;
  renderContextPackagePanel();
}

function renderProfileList(){
  const root = $('profileList');
  root.innerHTML = settings.agentProfiles.map(profile => `
    <article class="profile-card ${profile.id === settings.activeAgentId ? 'active' : ''}">
      <div class="profile-card-head">
        <div>
          <strong>${escapeHtml(profile.label)}</strong>
          <div class="profile-sub">${escapeHtml(typeLabel(profile.type))} · ${escapeHtml(adapterLabel(profile.adapter))} · ${escapeHtml(agentCapabilityLabel(profile))} · ${escapeHtml(sourceLabel(profile.source))}</div>
        </div>
        <div class="profile-pills">
          ${profile.id === settings.activeAgentId ? '<span class="pill active">当前</span>' : ''}
          ${profileStatusBadge(profile)}
          <span class="pill capability">${escapeHtml(agentCapabilityLabel(profile))}</span>
        </div>
      </div>
      <div class="profile-meta">${escapeHtml(profile.baseUrl || profile.binaryPath || '未设置地址')} ${profile.agentId ? `· agent:${escapeHtml(profile.agentId)}` : ''} ${profile.toolsProfile ? `· tools:${escapeHtml(profile.toolsProfile)}` : ''} ${profile.model ? `· ${escapeHtml(profile.model)}` : ''}</div>
      ${profileCapabilityHtml(profile)}
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
      if(action === 'edit') { settingsTab = 'agents'; editingProfileId = id; fillProfileForm(getProfile(id)); setSettingsTab(settingsTab); }
      if(action === 'clone') {
        settingsTab = 'agents';
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
          <div class="profile-sub">${escapeHtml(typeLabel(profile.type))} · ${escapeHtml(adapterLabel(profile.adapter))} · ${escapeHtml(agentCapabilityLabel(profile))}</div>
        </div>
        <div class="profile-pills">
          ${profileStatusBadge(profile)}
          <span class="pill capability">${escapeHtml(agentCapabilityLabel(profile))}</span>
        </div>
      </div>
      <div class="profile-meta">${escapeHtml(profile.baseUrl || profile.binaryPath || profile.configSource || '未找到连接地址')} ${profile.agentId ? `· agent:${escapeHtml(profile.agentId)}` : ''} ${profile.toolsProfile ? `· tools:${escapeHtml(profile.toolsProfile)}` : ''}</div>
      ${profileCapabilityHtml(profile, true)}
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
        settingsTab = 'agents';
        editingProfileId = null;
        fillProfileForm(found);
        setSettingsTab(settingsTab);
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
  const d = await AgentHubApi.saveAgents(payload);
  settings.agentProfiles = (d.agentHub?.profiles || settings.agentProfiles).map(normalizeProfile).filter(Boolean);
  settings.activeAgentId = d.agentHub?.activeAgentId || settings.activeAgentId;
  settings.configPath = d.configPath || settings.configPath;
  save();
}
async function createAgentHandoff(fromId, toId){
  const chat = activeChat();
  if(isFileMode || !chat || !fromId || !toId || fromId === toId) return null;
  const fromProfile = getProfile(fromId);
  const toProfile = getProfile(toId);
  const meaningful = (chat.messages || []).some(m => m.role === 'user' || m.role === 'assistant');
  if(!meaningful) return null;
  try{
    const payload = {
      sessionId: chat.id,
      fromAgentId: fromId,
      toAgentId: toId,
      fromAgentLabel: fromProfile?.label || fromId,
      toAgentLabel: toProfile?.label || toId,
      chat: {...chat, messages: chatsForStorage().find(item => item.id === chat.id)?.messages || chat.messages}
    };
    const d = await AgentHubApi.handoff(payload);
    chat.summary = d.summary || chat.summary || '';
    chat.handoffSummary = d.handoff || '';
    chat.handoffFromAgentId = fromId;
    chat.handoffFromLabel = fromProfile?.label || fromId;
    chat.handoffToAgentId = toId;
    chat.handoffToLabel = toProfile?.label || toId;
    chat.handoffAt = Date.now();
    chat.handoffDismissed = false;
    handoffRecords.unshift({
      session_id: chat.id,
      from_agent_id: fromId,
      to_agent_id: toId,
      handoff_summary: chat.handoffSummary,
      created_at: chat.handoffAt
    });
    handoffRecords = handoffRecords.slice(0, 20);
    chat.taskStatus = 'handoff';
    chat.updatedAt = Date.now();
    return d;
  }catch(e){
    console.warn('Agent Hub handoff skipped:', e.message);
    return null;
  }
}
async function setActiveProfile(id){
  const previousId = settings.activeAgentId;
  await createAgentHandoff(previousId, id);
  settings.activeAgentId = id;
  editingProfileId = id;
  const chat = activeChat();
  if(chat){
    chat.activeAgentId = id;
    chat.updatedAt = Date.now();
  }
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
    closeSettingsDialog();
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
  settingsTab = 'agents';
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
    const d = await AgentHubApi.health(headers);
    if(profile?.adapter === 'openclaw-gateway'){
      setBadge('unknown','Gateway 待接入');
    } else {
      setBadge(d.ok ? 'ok' : 'bad', d.ok ? `${activeAgentName()} 在线` : `${activeAgentName()} 异常`);
    }
    return d;
  }catch(e){ setBadge('bad','连接失败'); return {ok:false,error:e.message}; }
}
async function loadModels(){
  settingsTab = 'diagnostics';
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
    const d = await AgentHubApi.models(h); out.textContent=JSON.stringify(d,null,2);
    const id = d.data?.[0]?.id; if(id) $('model').value = id;
  }catch(e){ out.textContent='读取失败：'+e.message; }
}
function fileToDataURL(file){ return new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res(r.result); r.onerror=rej; r.readAsDataURL(file); }); }
function fileToBase64(file){ return new Promise((res,rej)=>{ const r=new FileReader(); r.onload=()=>res(String(r.result).split(',')[1] || ''); r.onerror=rej; r.readAsDataURL(file); }); }
async function parseRichDocument(file){
  if(isFileMode) return null;
  const payload = {name:file.name, mime:file.type || '', dataBase64: await fileToBase64(file)};
  const d = await AgentHubApi.parseAttachment(payload);
  return d.data || null;
}
async function fileToAttachment(file){
  if(file.type.startsWith('image/')) return {kind:'image', name:file.name, mime:file.type||'image/png', dataUrl:await fileToDataURL(file)};
  if(richDocExt.test(file.name)){
    const parsed = await parseRichDocument(file);
    if(parsed?.text) return {kind:'text', name:file.name, mime:file.type||parsed.mime||'text/plain', text:parsed.text, meta:parsed.meta || ''};
  }
  if(file.type.startsWith('text/') || textExt.test(file.name)){
    let text = await file.text(); if(text.length>30000) text = text.slice(0,30000)+'\n\n[文件过长，已截断]';
    return {kind:'text', name:file.name, mime:file.type||'text/plain', text};
  }
  return {kind:'file', name:file.name, mime:file.type||'application/octet-stream'};
}
function attachmentsToArtifacts(sessionId, messageId){
  return attachments.map((item, index) => ({
    id: `${sessionId}-a-${Date.now()}-${index}-${Math.random().toString(36).slice(2,6)}`,
    sessionId,
    messageId,
    kind: item.kind || 'file',
    name: item.name || 'attachment',
    mime: item.mime || '',
    text: item.text || '',
    dataUrl: item.dataUrl || '',
    includeInContext: true,
    createdAt: Date.now() + index
  }));
}
async function addFiles(files){
  for(const f of files){
    try{
      attachments.push(await fileToAttachment(f));
    }catch(e){
      const c = activeChat();
      if(c) c.messages.push({role:'error', content:`附件「${f.name}」处理失败：${e.message}`, meta:'附件解析失败'});
    }
  }
  save();
  renderAll();
  renderAttachments();
}
function renderAttachments(){
  $('attachments').innerHTML='';
  attachments.forEach((a,i)=>{
    const el=document.createElement('div'); el.className='att';
    el.innerHTML = `${a.kind==='image'?`<img src="${a.dataUrl}" />`:'📄'}<span>${escapeHtml(a.name)}</span>${a.meta ? `<small>${escapeHtml(a.meta)}</small>` : ''}<button>×</button>`;
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
  const userMessageId = uid();
  const content = buildContent(text);
  const attachmentCount = attachments.length;
  const artifactBatch = attachmentsToArtifacts(c.id, userMessageId);
  c.messages.push({id:userMessageId, role:'user', content, meta: attachments.length ? `${attachments.length} 个附件` : '', createdAt: Date.now(), agentId: settings.activeAgentId});
  c.artifacts = (c.artifacts || []).concat(artifactBatch);
  c.activeAgentId = settings.activeAgentId;
  if(!c.taskGoal && text) c.taskGoal = text.slice(0, 300);
  else if(!c.taskGoal) c.taskGoal = '基于当前附件完成分析与执行';
  c.taskStatus = 'active';
  c.nextStep = `等待 ${activeAgentName()} 返回本轮结果。`;
  c.updatedAt = Date.now();
  autoTitle(c, text || '附件分析');
  $('input').value=''; attachments=[]; renderAttachments();
  const progress = createProgressMessage(c, attachmentCount);
  const runId = uid();
  activeProgressId = progress.id;
  activeRun = {chatId:c.id, progressId:progress.id, runId, startedAt:Date.now()};
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
    const payload = {base_url:settings.baseUrl, api_key:settings.apiKey, model:settings.model, messages:apiMessages, temperature:0.3, stream:true, session_id:c.id, run_id:runId, workspace_path:c.workspacePath || '', approval_policy:c.approvalPolicy || 'auto', timeout: AGENT_RUN_TIMEOUT_SECONDS};
    const useLongRunningBridge = isOpenClawAdapter(profile) || isClaudeAdapter(profile) || profile?.supportsStream === false;
    if(useLongRunningBridge){
      progress.statusText = profile.adapter === 'openclaw-gateway-rpc'
        ? `正在执行 Gateway RPC agent:${profile.agentId || 'main'}`
        : profile.adapter === 'openclaw-cli'
        ? `正在执行 openclaw agent --agent ${profile.agentId || 'main'} --json`
        : profile.adapter === 'claude-code-cli'
        ? '正在执行 Claude Code CLI'
        : '正在通过长任务通道执行当前 Agent';
      markStep(progress, 2, 'active');
      progress.meta = `${agentModeSuffix(profile) || adapterLabel(profile.adapter)} · session:${c.id} · ${formatElapsed(Date.now() - progress.startedAt)}`;
      updateRunStatus(progress);
      renderMessages();
      await streamChat({...payload, stream:false, bridge:true, timeout: AGENT_RUN_TIMEOUT_SECONDS}, c, progress, started);
    } else {
      try {
        await streamChat(payload, c, progress, started);
      } catch(streamError) {
        if(isTimeoutError(streamError)) throw streamError;
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
    if(e?.name === 'AbortError'){
      if(c.messages.find(m => m.id === progress.id)) completeProgress(c, progress, '已停止当前任务。', false, {cancelled:true});
      setBadge('unknown','任务已停止');
      return;
    }
    const timeout = isTimeoutError(e);
    progress.statusText = timeout ? '任务超过当前等待时间，建议拆分任务或切换长任务 Agent' : '执行失败，请检查当前 Agent 或上游模型配置';
    const errorText = timeout
      ? `调用失败：任务执行超时。\n\n这类任务包含文件重设计、企业信息收集等长链路步骤，可能超过当前 Agent/上游模型一次请求的等待时间。\n\n建议：\n1. 先让 Agent 只处理 PDF 设计方案；\n2. 再单独处理 Excel 企业排序；\n3. 最后再做网上信息收集；\n4. 或切换到 OpenClaw / Claude Code 这类更适合长任务和本地执行的 Agent。\n\n原始错误：${e.message}`
      : '调用失败：'+e.message;
    if(c.messages.find(m => m.id === progress.id)) completeProgress(c, progress, errorText, true);
    else c.messages.push({id:uid(), role:'error', content:errorText, meta:`${formatElapsed(Date.now() - started)} · 执行失败`, createdAt: Date.now(), agentId: settings.activeAgentId});
    setBadge('bad', timeout ? '任务超时' : '调用失败');
  }finally{ activeProgressId = null; activeRun = null; activeAbortController = null; save(); renderAll(); setBusy(false); }
}
function exportMd(){
  const c=activeChat(); const lines=[`# ${c.title}\n`];
  c.messages.filter(m=>m.role!=='system').forEach(m=>lines.push(`## ${m.role}\n\n${displayContent(m.content)}\n`));
  const blob=new Blob([lines.join('\n')],{type:'text/markdown'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=(c.title||'agent-chat')+'.md'; a.click();
}

$('newChat').onclick=()=>createChat(true);
$('searchChat').oninput=renderChatList;
$('taskSort').onchange=e=>{ taskSort = e.target.value || 'updated_desc'; save(); renderChatList(); };
$('currentAgentOnly').onclick=()=>{ currentAgentOnly = !currentAgentOnly; save(); renderChatList(); };
$('settingsBtn').onclick=()=>openSettingsDialog('overview');
$('closeSettings').onclick=closeSettingsDialog;
$('closeRunDebug').onclick=closeRunDebugPanel;
$('taskOverview')?.addEventListener('click', event => {
  const chromeButton = event.target.closest?.('[data-task-chrome]');
  if(chromeButton){
    event.preventDefault();
    event.stopPropagation();
    if(chromeButton.dataset.taskChrome === 'toggle') setTaskChromeCollapsed(!taskChromeCollapsed);
    if(chromeButton.dataset.taskChrome === 'scale') setTaskChromeScale(chromeButton.dataset.scale || 'compact');
    if(chromeButton.dataset.taskChrome === 'pin') setTaskChromePinned(!taskChromePinned);
    return;
  }
  if(event.target.closest?.('.task-overview-compact-line')){
    setTaskChromeCollapsed(!taskChromeCollapsed);
  }
});
$('saveSettings').onclick=saveCurrentProfile;
$('deleteProfileBtn').onclick=deleteCurrentProfile;
$('modelsBtn').onclick=loadModels;
$('refreshActiveModelsBtn').onclick=refreshModelOptions;
$('addCustomModelBtn').onclick=promptCustomModel;
$('healthBtn').onclick=async()=>{ const d=await health(); alert(JSON.stringify(d,null,2)); };
$('discoverBtn').onclick=discoverAgents;
$('refreshContextBtn').onclick=refreshContextPanel;
$('refreshContextPackageBtn').onclick=refreshContextPackage;
$('contextStrategySelect').onchange=e=>setContextStrategy(e.target.value || 'standard');
$('refreshMemoriesBtn').onclick=refreshProjectMemories;
$('extractMemoriesBtn').onclick=extractMemoriesFromCurrentTask;
$('inspectWorkspaceBtn').onclick=inspectAndBindWorkspace;
$('clearWorkspaceBtn').onclick=clearWorkspace;
$('newProfileBtn').onclick=startNewProfile;
$('exportBtn').onclick=exportMd;
$('attachBtn').onclick=()=>$('fileInput').click();
$('composerMode').onclick=(event)=>{ event.stopPropagation(); toggleAgentSwitcher(); };
$('modelMode').onclick=(event)=>{ event.stopPropagation(); toggleModelSwitcher(); };
$('dismissHandoffBtn').onclick=()=>{
  const chat = activeChat();
  if(!chat) return;
  chat.handoffDismissed = true;
  chat.updatedAt = Date.now();
  save();
  renderHandoffBanner();
};
document.querySelectorAll('[data-settings-tab]').forEach(btn => {
  btn.onclick = () => setSettingsTab(btn.dataset.settingsTab || 'overview');
});
$('composerMode').addEventListener('keydown', event => {
  if(event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' '){
    event.preventDefault();
    openAgentSwitcher();
    $('agentSwitchMenu')?.querySelector('.agent-switch-option')?.focus();
  }
});
$('modelMode').addEventListener('keydown', event => {
  if(event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' '){
    event.preventDefault();
    openModelSwitcher();
    $('modelSwitchMenu')?.querySelector('.model-switch-option')?.focus();
  }
});
$('fileInput').onchange=e=>addFiles(e.target.files);
$('sendBtn').onclick=send;
$('cancelRunBtn').onclick=cancelActiveRun;
$('cancelComposerBtn').onclick=cancelActiveRun;
$('input').addEventListener('keydown', e=>{ if((e.metaKey||e.ctrlKey)&&e.key==='Enter') send(); });
for(const b of document.querySelectorAll('.prompt-card')) b.onclick=()=>{ $('input').value=b.textContent; $('input').focus(); };
window.addEventListener('dragover', e=>{ e.preventDefault(); document.body.classList.add('dragging'); });
window.addEventListener('dragleave', ()=>document.body.classList.remove('dragging'));
window.addEventListener('drop', e=>{ e.preventDefault(); document.body.classList.remove('dragging'); addFiles(e.dataTransfer.files); });
window.addEventListener('click', e=>{
  if(!$('agentSwitcher')?.contains(e.target)) closeAgentSwitcher();
  if(!$('modelSwitcher')?.contains(e.target)) closeModelSwitcher();
});
$('settingsModal')?.addEventListener('click', e=>{ if(e.target === $('settingsModal')) closeSettingsDialog(); });
$('runDebugModal')?.addEventListener('click', e=>{ if(e.target === $('runDebugModal')) closeRunDebugPanel(); });
window.addEventListener('keydown', e=>{
  if(e.key === 'Escape'){
    if(!$('settingsModal')?.classList.contains('hidden')) closeSettingsDialog();
    if(!$('runDebugModal')?.classList.contains('hidden')) closeRunDebugPanel();
    closeAgentSwitcher();
    closeModelSwitcher();
  }
  if(isAgentSwitcherOpen()){
    const options = [...($('agentSwitchMenu')?.querySelectorAll('.agent-switch-option') || [])];
    const index = options.indexOf(document.activeElement);
    if(e.key === 'ArrowDown'){
      e.preventDefault();
      options[Math.min(index + 1, options.length - 1)]?.focus();
    }
    if(e.key === 'ArrowUp'){
      e.preventDefault();
      options[Math.max(index - 1, 0)]?.focus();
    }
  }
  if(isModelSwitcherOpen()){
    const options = [...($('modelSwitchMenu')?.querySelectorAll('.model-switch-option') || [])];
    const index = options.indexOf(document.activeElement);
    if(e.key === 'ArrowDown'){
      e.preventDefault();
      options[Math.min(index + 1, options.length - 1)]?.focus();
    }
    if(e.key === 'ArrowUp'){
      e.preventDefault();
      options[Math.max(index - 1, 0)]?.focus();
    }
  }
});
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
  if(adapter === 'claude-code-cli'){
    if(!$('profileAgentId').value.trim()) $('profileAgentId').value = 'claude-code';
    if(!$('baseUrl').value.trim()) $('baseUrl').value = 'claude';
    if(!$('model').value.trim()) $('model').value = 'sonnet';
    $('profileType').value = 'claude';
  }
});

(async function init(){
  load();
  await readConfig();
  renderAll();
  const result = await health();
  if(isFileMode || result?.error === 'preview-mode') showBootNotice(true);
})();
