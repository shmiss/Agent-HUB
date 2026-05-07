#!/usr/bin/env node
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const PORT = Number(process.env.AGENT_HUB_SMOKE_PORT || 18765);
const BASE = `http://127.0.0.1:${PORT}`;
const CHROME = process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const DEBUG_PORT = Number(process.env.AGENT_HUB_SMOKE_DEBUG_PORT || 19223);

function wait(ms){ return new Promise(resolve => setTimeout(resolve, ms)); }
function request(url, options = {}){
  return new Promise((resolve, reject) => {
    const req = http.request(url, options, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => body += chunk);
      res.on('end', () => resolve({statusCode: res.statusCode, body}));
    });
    req.on('error', reject);
    if(options.body) req.write(options.body);
    req.end();
  });
}
async function waitForHttp(url, timeoutMs = 10000){
  const started = Date.now();
  while(Date.now() - started < timeoutMs){
    try{
      const res = await request(url);
      if(res.statusCode && res.statusCode < 500) return res;
    }catch{}
    await wait(200);
  }
  throw new Error(`Timed out waiting for ${url}`);
}
async function waitForDevtools(timeoutMs = 10000){
  const started = Date.now();
  while(Date.now() - started < timeoutMs){
    try{
      const res = await request(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      if(res.statusCode === 200) return JSON.parse(res.body);
    }catch{}
    await wait(200);
  }
  throw new Error('Timed out waiting for Chrome DevTools');
}
function assert(condition, message){
  if(!condition) throw new Error(message);
}
async function cdp(page){
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const exceptions = [];
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if(msg.id && pending.has(msg.id)){
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
    if(msg.method === 'Runtime.exceptionThrown'){
      exceptions.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    }
  };
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });
  function send(method, params = {}){
    return new Promise(resolve => {
      const mid = ++id;
      pending.set(mid, resolve);
      ws.send(JSON.stringify({id: mid, method, params}));
    });
  }
  async function evaluate(expression){
    const msg = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
    if(msg.result?.exceptionDetails){
      throw new Error(msg.result.exceptionDetails.exception?.description || msg.result.exceptionDetails.text || 'Runtime.evaluate failed');
    }
    return msg.result?.result?.value;
  }
  await send('Runtime.enable');
  await send('Page.enable');

  async function waitForExpression(expression, timeoutMs = 8000){
    const started = Date.now();
    while(Date.now() - started < timeoutMs){
      const value = await evaluate(expression);
      if(value) return value;
      await wait(200);
    }
    throw new Error(`Timed out waiting for expression: ${expression}`);
  }
  return {send, evaluate, waitForExpression, exceptions, close: () => ws.close()};
}

(async function main(){
  const chromeProfile = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-hub-smoke-chrome-'));
  const smokeDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-hub-smoke-data-'));
  const server = spawn('python3', ['server.py'], {
    cwd: ROOT,
    env: {...process.env, HERMES_UI_PORT: String(PORT), AGENT_HUB_DATA_DIR: smokeDataDir},
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const chrome = spawn(CHROME, [
    '--headless=new',
    '--disable-gpu',
    `--remote-debugging-port=${DEBUG_PORT}`,
    `--user-data-dir=${chromeProfile}`,
    BASE,
  ], {stdio: ['ignore', 'pipe', 'pipe']});

  let cleaned = false;
  const cleanup = () => {
    if(cleaned) return;
    cleaned = true;
    try{ chrome.kill('SIGTERM'); }catch{}
    try{ server.kill('SIGTERM'); }catch{}
    try{
      fs.rmSync(smokeDataDir, {force: true, recursive: true});
      fs.rmSync(chromeProfile, {force: true, recursive: true});
    }catch{}
  };
  process.on('exit', cleanup);
  process.on('SIGINT', () => { cleanup(); process.exit(130); });

  try{
    await waitForHttp(`${BASE}/`, 12000);
    const pages = await waitForDevtools(12000);
    const page = pages.find(item => item.url === `${BASE}/` || item.url.startsWith(`${BASE}/?`));
    assert(page?.webSocketDebuggerUrl, 'Chrome page not found');
    const client = await cdp(page);
    await client.waitForExpression(`document.querySelectorAll('#taskFilters button').length >= 1 && document.querySelectorAll('#chatList .task-card').length >= 1 && typeof AgentHubContext === 'object'`, 10000);

    const initial = await client.evaluate(`({
      title: document.title,
      filters: document.querySelectorAll('#taskFilters button').length,
      chatCards: document.querySelectorAll('#chatList .task-card').length,
      contextStatsType: typeof currentContextStats,
      messagesForApiType: typeof messagesForApi,
      agentHubContextType: typeof AgentHubContext,
      visibleText: document.body.innerText.slice(0, 200),
    })`);
    assert(initial.title === 'Agent Hub', 'page title should be Agent Hub');
    assert(initial.filters >= 1, 'task filters should render');
    assert(initial.chatCards >= 1, 'task cards should render');
    assert(initial.contextStatsType === 'function', 'currentContextStats should be available');
    assert(initial.messagesForApiType === 'function', 'messagesForApi should be available');
    assert(initial.agentHubContextType === 'object', 'AgentHubContext should be loaded');

    const afterNew = await client.evaluate(`(async()=>{
      const before = document.querySelectorAll('#chatList .task-card').length;
      document.getElementById('newChat').click();
      await new Promise(r => setTimeout(r, 300));
      return {before, after: document.querySelectorAll('#chatList .task-card').length, title: document.getElementById('chatTitle').textContent};
    })()`);
    assert(afterNew.after >= afterNew.before, 'new chat should not reduce task cards');
    assert(afterNew.title.includes('新的会话') || afterNew.title.includes('新的企业智能体会话'), 'new chat should update active title');

    const taskChrome = await client.evaluate(`(async()=>{
      const before = document.getElementById('taskOverview').classList.contains('task-chrome-collapsed');
      const pinnedBefore = document.getElementById('taskOverview').classList.contains('task-chrome-pinned');
      const positionBefore = getComputedStyle(document.getElementById('taskOverview')).position;
      document.querySelector('[data-task-chrome="toggle"]').click();
      await new Promise(r => setTimeout(r, 250));
      const expanded = !document.getElementById('taskOverview').classList.contains('task-chrome-collapsed');
      document.querySelector('[data-task-chrome="toggle"]').click();
      await new Promise(r => setTimeout(r, 250));
      const collapsedAgain = document.getElementById('taskOverview').classList.contains('task-chrome-collapsed');
      return {
        before,
        pinnedBefore,
        positionBefore,
        expanded,
        collapsedAgain,
        detailDisplay: getComputedStyle(document.getElementById('taskDetailPanels')).display,
        toggleText: document.querySelector('[data-task-chrome="toggle"]').textContent
      };
    })()`);
    assert(taskChrome.expanded === true, 'task chrome should expand after toggle click');
    assert(taskChrome.pinnedBefore === true, 'task chrome should be pinned by default');
    assert(taskChrome.positionBefore === 'sticky', 'pinned task chrome should use sticky positioning');
    assert(taskChrome.collapsedAgain === true, 'task chrome should collapse after second toggle click');
    assert(taskChrome.detailDisplay === 'none', 'task detail panels should hide after collapse');
    assert(taskChrome.toggleText === '展开', 'task toggle should show expand after collapse');

    const routerAndModel = await client.evaluate(`(async()=>{
      taskChromeCollapsed = false;
      const chat = activeChat();
      settings.agentProfiles = [
        {id:'smoke-hermes', label:'Hermes Smoke', type:'hermes', adapter:'openai-chat', baseUrl:'http://127.0.0.1:8642/v1', model:'hermes-agent'},
        {id:'smoke-claude', label:'Claude Smoke', type:'claude', adapter:'claude-code-cli', baseUrl:'claude', model:'sonnet'}
      ];
      settings.activeAgentId = 'smoke-hermes';
      modelRegistry = {};
      renderAll();
      AgentHubApi.taskRouterRecommend = async () => ({
        recommendation: {
          id: 'smoke-rec',
          sessionId: chat.id,
          taskType: 'code',
          taskTypeLabel: '代码任务',
          primaryAgentId: settings.activeAgentId,
          primaryAgentLabel: activeAgentName(),
          contextStrategy: 'code',
          confidence: 0.88,
          reasons: ['命中关键词：bug', '当前任务已绑定工作区'],
          warnings: []
        }
      });
      await recommendAgentForTask(chat);
      document.querySelector('[data-router-action="detail"]').click();
      await setActiveModel('smoke-hermes-model');
      const hermesMenu = document.getElementById('modelSwitchMenu').innerText;
      await setActiveProfile('smoke-claude');
      renderAll();
      const claudeMenu = document.getElementById('modelSwitchMenu').innerText;
      return {
        routerText: document.querySelector('.task-router-strip')?.innerText || '',
        routerDetailText: document.querySelector('.task-router-detail')?.innerText || '',
        modelLabel: document.getElementById('modelModeLabel')?.textContent || '',
        profileModel: getActiveProfile()?.model || '',
        hermesMenu,
        claudeMenu
      };
    })()`);
    assert(/推荐|代码任务|88/.test(routerAndModel.routerText), 'task router recommendation should render');
    assert(/命中关键词/.test(routerAndModel.routerDetailText), 'task router details should render reasons');
    assert(routerAndModel.modelLabel === 'sonnet', 'model switcher label should update for active agent');
    assert(routerAndModel.profileModel === 'sonnet', 'active profile model should stay agent-specific');
    assert(/smoke-hermes-model/.test(routerAndModel.hermesMenu), 'hermes model menu should include hermes model');
    assert(!/smoke-hermes-model/.test(routerAndModel.claudeMenu), 'claude model menu should not include hermes model');
    assert(/opus|default/.test(routerAndModel.claudeMenu), 'claude model menu should show claude defaults');

    const workflowUi = await client.evaluate(`(async()=>{
      const chat = activeChat();
      taskSpec = {sessionId: chat.id, goal: '修复冒烟任务', taskType: 'code', stage: 'planning', acceptance: ['测试通过'], constraints: ['保持最小改动'], finalDeliverable: '可验证补丁'};
      taskWorkflow = {sessionId: chat.id, currentStage: 'planning', current: {allowedNext:['draft','executing','review'], nextStep:'确认后进入执行'}};
      AgentHubApi.transitionTaskStage = async () => ({
        data: {sessionId: chat.id, currentStage: 'executing', current: {label:'执行', status:'active', nextStep:'进入执行', allowedNext:['review','testing','done']}},
        recommendation: {id:'stage-rec', taskType:'code', primaryAgentId: settings.activeAgentId, primaryAgentLabel: activeAgentName(), contextStrategy:'code', confidence:0.91, reasons:['当前 Workflow 阶段：执行'], warnings:[]}
      });
      AgentHubApi.workflowStageAction = async () => ({
        data: {stage:'executing', stageLabel:'执行', title:'Agent 执行指令包', content:'# Agent 执行指令包\\n请完成冒烟执行'},
        recommendation: {id:'stage-action-rec', taskType:'code', primaryAgentId: settings.activeAgentId, primaryAgentLabel: activeAgentName(), contextStrategy:'code', confidence:0.92, reasons:['阶段动作推荐'], warnings:[]}
      });
      taskChromeCollapsed = false;
      renderTaskOverview();
      document.querySelector('[data-workflow-stage="executing"]').click();
      await new Promise(r => setTimeout(r, 100));
      document.querySelector('[data-workflow-action="stage-action"]').click();
      await new Promise(r => setTimeout(r, 100));
      return {
        specText: document.querySelector('.task-spec-strip')?.innerText || '',
        workflowText: document.querySelector('.task-workflow-strip')?.innerText || '',
        activeStage: document.querySelector('.task-workflow-rail button.active')?.textContent || '',
        stageButtons: document.querySelectorAll('.task-workflow-rail button').length,
        routerText: document.querySelector('.task-router-strip')?.innerText || '',
        nextStep: activeChat().nextStep || '',
        latestMessage: document.querySelector('#messages .message:last-child')?.innerText || '',
        actionButton: document.querySelector('[data-workflow-action="stage-action"]')?.textContent || '',
        sendButton: document.querySelector('[data-workflow-action="send-action"]')?.textContent || ''
      };
    })()`);
    assert(/Task Spec|可验证补丁/.test(workflowUi.specText), 'task spec strip should render');
    assert(/Workflow|进入执行/.test(workflowUi.workflowText), 'workflow strip should render');
    assert(/执行/.test(workflowUi.activeStage), 'workflow should highlight current stage after transition');
    assert(workflowUi.stageButtons === 7, 'workflow should render seven stage buttons');
    assert(/推荐|91/.test(workflowUi.routerText), 'workflow transition should refresh router recommendation');
    assert(/生成执行指令/.test(workflowUi.actionButton), 'workflow should render stage action button');
    assert(/发送给推荐 Agent/.test(workflowUi.sendButton), 'workflow should render send-to-recommended-agent button after stage action');
    assert(/阶段动作|采用推荐 Agent/.test(workflowUi.nextStep), 'workflow action should update next step');
    assert(/Agent 执行指令包|冒烟执行/.test(workflowUi.latestMessage), 'workflow stage action should insert assistant message');

    const runWorkflowLink = await client.evaluate(`(async()=>{
      const chat = activeChat();
      taskSpec = {sessionId: chat.id, goal: '自动联动', taskType: 'code', stage: 'executing'};
      taskWorkflow = {sessionId: chat.id, currentStage: 'executing', current: {label:'执行', status:'active', allowedNext:['review']}};
      AgentHubApi.transitionTaskStage = async (payload) => ({
        data: {sessionId: chat.id, currentStage: payload.stage, current: {label:'复核', status:'handoff', nextStep:'进入复核', allowedNext:['testing','done']}},
        recommendation: {id:'run-link-rec', taskType:'review', primaryAgentId: settings.activeAgentId, primaryAgentLabel: activeAgentName(), contextStrategy:'full', confidence:0.9, reasons:['Agent Run 成功后进入复核'], warnings:[]}
      });
      await autoLinkWorkflowAfterRun(chat, 'success');
      await new Promise(r => setTimeout(r, 50));
      return {
        stage: taskWorkflow?.currentStage || '',
        status: chat.taskStatus || '',
        nextStep: chat.nextStep || '',
        router: taskRecommendation?.taskType || ''
      };
    })()`);
    assert(runWorkflowLink.stage === 'review', 'successful run should advance workflow to review');
    assert(runWorkflowLink.status === 'handoff', 'workflow auto link should update task status');
    assert(/自动进入|复核/.test(runWorkflowLink.nextStep), 'workflow auto link should update next step');
    assert(runWorkflowLink.router === 'review', 'workflow auto link should refresh router recommendation');

    const registryUi = await client.evaluate(`(async()=>{
      settingsTab = 'models';
      renderSettings();
      await new Promise(r => setTimeout(r, 100));
      return {
        activeTab: document.querySelector('[data-settings-panel="models"]').classList.contains('active'),
        cards: document.querySelectorAll('.model-registry-card').length,
        text: document.getElementById('modelRegistryPanel').innerText
      };
    })()`);
    assert(registryUi.activeTab === true, 'model registry tab should activate');
    assert(registryUi.cards >= 2, 'model registry should render agent model cards');
    assert(/Hermes Smoke|Claude Smoke|sonnet/.test(registryUi.text), 'model registry should show per-agent models');

    const timeline = await client.evaluate(`(async()=>{
      const chat = activeChat();
      agentRuns = [{
        id: 'smoke-run-1',
        session_id: chat.id,
        agent_id: 'detected-openclaw-local-main',
        adapter: 'openclaw-gateway-rpc',
        status: 'success',
        latency_ms: 1234,
        output_summary: '完成一次冒烟执行记录',
        created_at: Date.now(),
        debug: {agentLabel:'OpenClaw Smoke', model:'smoke-model', messages:[{role:'user', text:'hello'}], contextPackageText:'Agent Hub Context Package v1'}
      }];
      handoffRecords = [{
        session_id: chat.id,
        from_agent_id: 'detected-hermes-local',
        to_agent_id: 'detected-openclaw-local-main',
        handoff_summary: '交接方向：Hermes 本机 → OpenClaw 本机\\n当前任务状态：冒烟测试任务',
        created_at: Date.now()
      }];
      taskChromeCollapsed = false;
      renderTaskDetailPanels();
      await new Promise(r => setTimeout(r, 100));
      return {
        runItems: document.querySelectorAll('.run-timeline-item').length,
        handoffItems: document.querySelectorAll('.handoff-timeline-item').length,
        runActions: document.querySelectorAll('.run-timeline-actions button').length,
        text: document.getElementById('taskDetailPanels').innerText
      };
    })()`);
    assert(timeline.runItems === 1, 'run timeline should render injected run');
    assert(timeline.handoffItems === 1, 'handoff timeline should render injected handoff');
    assert(timeline.runActions >= 3, 'run timeline should render debug actions');
    assert(/Agent Run Timeline|OpenClaw|完成一次冒烟执行记录/.test(timeline.text), 'run timeline should show agent and summary');
    assert(/Handoff Timeline|Hermes|OpenClaw|冒烟测试任务/.test(timeline.text), 'handoff timeline should show direction and summary');

    const packageUi = await client.evaluate(`(async()=>{
      AgentHubApi.contextPackage = async () => ({
        package: {
          schema: 'agent-hub.context-package.v1',
          sessionId: activeChat().id,
          strategy: activeChat().contextStrategy || 'standard',
          title: '冒烟上下文包',
          targetAgentLabel: 'OpenClaw 本机',
          task: {goal: '验证 Context Package'},
          stats: {messages: 2, recentMessages: 2, artifacts: 1, pinnedArtifacts: 1, runs: 1, handoffs: 1, memories: 1, chars: 300}
        },
        text: '[Agent Hub Context Package v1]\\n任务：冒烟上下文包\\n目标：验证 Context Package'
      });
      document.getElementById('contextStrategySelect').value = 'file';
      document.getElementById('contextStrategySelect').dispatchEvent(new Event('change'));
      await refreshContextPackage();
      return {
        text: document.getElementById('contextPackagePanel').innerText,
        cards: document.querySelectorAll('.context-package-card').length,
        strategy: activeChat().contextStrategy,
        hint: document.getElementById('contextStrategyHint').innerText
      };
    })()`);
    assert(packageUi.cards === 1, 'context package panel should render card');
    assert(packageUi.strategy === 'file', 'context strategy selector should update active chat');
    assert(/文件任务/.test(packageUi.hint + packageUi.text), 'context strategy should render in UI');
    assert(/Context Package|验证 Context Package|agent-hub.context-package.v1/.test(packageUi.text), 'context package panel should show package content');

    const workspaceUi = await client.evaluate(`(async()=>{
      AgentHubApi.inspectWorkspace = async (path) => ({
        workspace: {path, name: 'agent-hub-smoke', exists: true, isDir: true, isGitRepo: true, gitBranch: 'main', gitDirty: true, checkedAt: Date.now()}
      });
      document.getElementById('workspacePathInput').value = '/tmp/agent-hub-smoke';
      await inspectAndBindWorkspace();
      return {
        path: activeChat().workspacePath,
        name: activeChat().workspaceName,
        text: document.getElementById('workspaceStatus').innerText + ' ' + document.getElementById('taskOverview').innerText,
        dock: document.querySelectorAll('.task-workspace-dock.bound').length,
        chip: document.querySelectorAll('.task-workspace-chip.bound').length,
        dockButtons: document.querySelectorAll('.task-workspace-actions button').length,
        policyOptions: document.querySelectorAll('[data-approval-policy] option').length
      };
    })()`);
    assert(workspaceUi.path === '/tmp/agent-hub-smoke', 'workspace path should bind to active task');
    assert(workspaceUi.name === 'agent-hub-smoke', 'workspace name should bind to active task');
    assert(workspaceUi.dock === 1, 'workspace dock should render inside task overview');
    assert(workspaceUi.chip === 1, 'workspace chip should render in task overview compact line');
    assert(workspaceUi.dockButtons >= 2, 'workspace dock should expose change and clear actions');
    assert(workspaceUi.policyOptions >= 3, 'workspace dock should expose approval policy choices');
    assert(/agent-hub-smoke|main|有变更/.test(workspaceUi.text), 'workspace status should render git metadata');

    const memoryUi = await client.evaluate(`(async()=>{
      AgentHubApi.memories = async () => ({data:[{id:'mem1', kind:'command', title:'测试命令', content:'node tests/smoke_frontend.js'}]});
      AgentHubApi.extractMemories = async () => ({data:[]});
      await refreshProjectMemories();
      return {
        cards: document.querySelectorAll('.memory-card').length,
        text: document.getElementById('projectMemoryPanel').innerText
      };
    })()`);
    assert(memoryUi.cards === 1, 'project memory panel should render memory cards');
    assert(/测试命令|smoke_frontend/.test(memoryUi.text), 'project memory panel should show memory content');

    const afterSend = await client.evaluate(`(async()=>{
      AgentHubApi.chatStream = async () => new Promise(() => {});
      AgentHubApi.chat = async () => new Promise(() => {});
      document.getElementById('input').value = 'smoke test: 只进入处理中状态';
      document.getElementById('sendBtn').click();
      await new Promise(r => setTimeout(r, 800));
      return {
        busy: document.getElementById('sendBtn').textContent,
        cancelHidden: document.getElementById('cancelComposerBtn').classList.contains('hidden'),
        cancelText: document.getElementById('cancelComposerBtn').textContent,
        runHidden: document.getElementById('runStatus').classList.contains('hidden'),
        visibleMessages: document.querySelectorAll('#messages .message').length,
        emptyDisplay: getComputedStyle(document.getElementById('emptyState')).display,
      };
    })()`);
    assert(afterSend.busy === '处理中…', 'send button should enter busy state');
    assert(afterSend.cancelHidden === false && /停止/.test(afterSend.cancelText), 'composer cancel button should show while running');
    assert(afterSend.runHidden === false, 'run status should show after send');
    assert(afterSend.visibleMessages >= 1, 'messages should render after send');
    assert(afterSend.emptyDisplay === 'none', 'empty state should hide after send');

    const afterDiscover = await client.evaluate(`(async()=>{
      document.getElementById('settingsBtn').click();
      await new Promise(r => setTimeout(r, 250));
      document.querySelector('[data-settings-tab="discovery"]').click();
      document.getElementById('discoverBtn').click();
      await new Promise(r => setTimeout(r, 2500));
      return {
        modalHidden: document.getElementById('settingsModal').classList.contains('hidden'),
        discoveryText: document.getElementById('discoverList').innerText,
        output: document.getElementById('settingsOutput').textContent,
        capabilityCards: document.querySelectorAll('.capability-card').length,
        capabilityText: document.body.innerText,
      };
    })()`);
    assert(afterDiscover.modalHidden === false, 'settings modal should open');
    assert(!/扫描失败/.test(afterDiscover.output), 'discover should not report scan failure');
    assert(/扫描完成|暂未扫描|Hermes|OpenClaw|Claude/.test(afterDiscover.output + afterDiscover.discoveryText), 'discover should produce a result');
    assert(afterDiscover.capabilityCards >= 1, 'settings should render agent capability cards');
    assert(/通用规划型 Agent|本地执行型 Agent|代码型 Agent|自定义接入/.test(afterDiscover.capabilityText), 'capability card role should render');

    assert(client.exceptions.length === 0, `browser runtime exceptions: ${client.exceptions.join('\n')}`);
    client.close();
    console.log('Frontend smoke OK');
  } finally {
    cleanup();
  }
})().catch(err => {
  console.error(err.stack || err.message);
  process.exit(1);
});
