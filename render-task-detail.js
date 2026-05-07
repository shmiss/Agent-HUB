(function(){


  function handoffAgentLabel(agentId, deps){
    const profile = deps.getProfile?.(agentId);
    if(profile?.label) return profile.label;
    if(!agentId) return '未指定 Agent';
    if(String(agentId).includes('hermes')) return 'Hermes 本机';
    if(String(agentId).includes('openclaw')) return 'OpenClaw 本机';
    if(String(agentId).includes('claude')) return 'Claude Code 本机';
    return String(agentId);
  }

  function handoffAvatar(agentId){
    const text = String(agentId || '').toLowerCase();
    if(text.includes('openclaw')) return '🦞';
    if(text.includes('claude')) return '◇';
    if(text.includes('hermes')) return '🐴';
    if(text.includes('codex')) return '⌘';
    return '🤝';
  }

  function handoffSummaryText(item){
    return item?.handoff_summary || item?.text || item?.summary || '';
  }

  function runStatusLabel(status){
    if(status === 'success') return '完成';
    if(status === 'error') return '失败';
    if(status === 'running') return '运行中';
    if(status === 'cancelled') return '已取消';
    return status || '记录';
  }

  function runStatusClass(status){
    if(status === 'success') return 'success';
    if(status === 'error') return 'error';
    if(status === 'running') return 'running';
    if(status === 'cancelled') return 'cancelled';
    return 'unknown';
  }

  function agentAvatarFromRun(run){
    const text = `${run?.agent_id || ''} ${run?.adapter || ''}`.toLowerCase();
    if(text.includes('openclaw')) return '🦞';
    if(text.includes('claude')) return '◇';
    if(text.includes('hermes')) return '🐴';
    if(text.includes('codex')) return '⌘';
    return '🤖';
  }

  function runAgentLabel(run, deps){
    const profile = deps.getProfile?.(run?.agent_id);
    if(profile?.label) return profile.label;
    const text = `${run?.agent_id || run?.adapter || 'agent'}`;
    if(text.includes('detected-hermes')) return 'Hermes 本机';
    if(text.includes('openclaw')) return 'OpenClaw 本机';
    if(text.includes('claude')) return 'Claude Code 本机';
    return text;
  }

  const workflowStages = [
    ['draft', '草稿'],
    ['planning', '规划'],
    ['executing', '执行'],
    ['review', '复核'],
    ['testing', '测试'],
    ['done', '完成'],
    ['archived', '归档'],
  ];

  function workflowActionLabel(stage){
    const map = {
      draft: '澄清需求',
      planning: '生成执行计划',
      executing: '生成执行指令',
      review: '生成 Review 清单',
      testing: '生成验收清单',
      done: '生成交付摘要',
      archived: '生成归档记忆',
    };
    return map[stage] || '生成阶段动作';
  }

  function renderTaskOverview(chat, deps = {}){
    const $ = deps.$;
    const escapeHtml = deps.escapeHtml || ((value) => String(value ?? ''));
    const root = $?.('taskOverview');
    if(!root) return;
    if(!chat){
      root.innerHTML = '';
      return;
    }
    const profile = deps.getProfile?.(chat.activeAgentId) || deps.getActiveProfile?.();
    const workspaceText = deps.workspaceLabel?.(chat) || (chat.workspacePath ? chat.workspacePath : '未绑定项目');
    const workspaceBound = !!chat.workspacePath;
    const artifactsCount = Number((chat.artifacts || []).length);
    const messagesCount = Number((chat.messages || []).filter(m => m.role !== 'system').length);
    const status = chat.taskStatus || 'todo';
    const collapsed = deps.taskChromeCollapsed !== false;
    const scale = ['compact','normal','large'].includes(deps.taskChromeScale) ? deps.taskChromeScale : 'compact';
    const pinned = deps.taskChromePinned !== false;
    const recommendation = deps.taskRecommendation?.sessionId === chat.id ? deps.taskRecommendation : null;
    const recommendationBusy = !!deps.taskRecommendationBusy;
    const taskSpec = deps.taskSpec?.sessionId === chat.id ? deps.taskSpec : null;
    const workflow = deps.taskWorkflow?.sessionId === chat.id ? deps.taskWorkflow : null;
    const latestAction = deps.latestWorkflowAction?.sessionId === chat.id ? deps.latestWorkflowAction : null;
    const currentStage = workflow?.currentStage || taskSpec?.stage || (status === 'todo' ? 'draft' : status === 'done' ? 'done' : status === 'handoff' ? 'review' : 'executing');
    const allowedNext = new Set(workflow?.current?.allowedNext || workflowStages.map(item => item[0]));
    const recAgent = recommendation?.primaryAgentId ? deps.getProfile?.(recommendation.primaryAgentId) : null;
    const recReasons = Array.isArray(recommendation?.reasons) ? recommendation.reasons : [];
    const recWarnings = Array.isArray(recommendation?.warnings) ? recommendation.warnings : [];
    root.classList.toggle('task-chrome-collapsed', collapsed);
    root.classList.toggle('task-chrome-pinned', pinned);
    root.classList.remove('task-chrome-scale-compact', 'task-chrome-scale-normal', 'task-chrome-scale-large');
    root.classList.add(`task-chrome-scale-${scale}`);
    const statusActions = [
      ['todo', '待开始'],
      ['active', '进行中'],
      ['blocked', '阻塞'],
      ['handoff', '待交接'],
      ['done', '完成'],
    ];
    root.innerHTML = `
      <div class="task-overview-card task-console-card ${collapsed ? 'is-collapsed' : 'is-expanded'}" role="region" aria-label="任务状态摘要">
        <div class="task-overview-main">
          <div class="task-overview-headline">
            <div>
              <div class="task-overview-kicker">TASK COMMAND CENTER</div>
              <div class="task-overview-compact-line">
                <h2 class="task-overview-title">${escapeHtml(chat.title || '新的任务')}</h2>
                <span class="task-chip ${deps.chatStatusClass?.(chat) || ''}">${escapeHtml(deps.chatStatusLabel?.(chat) || '进行中')}</span>
                <span class="task-chip">${escapeHtml(profile?.label || '未指定 Agent')}</span>
                <button type="button" class="task-workspace-chip ${workspaceBound ? 'bound' : 'empty'}" data-workspace-action="bind" title="${escapeHtml(workspaceBound ? chat.workspacePath : '可选：为当前任务绑定本地项目目录')}">
                  项目 · ${escapeHtml(workspaceBound ? workspaceText : '按需绑定')}
                </button>
                <span class="task-mini-stat">附件 ${artifactsCount}</span>
                <span class="task-mini-stat">交接 ${chat.handoffSummary ? 'ON' : 'OFF'}</span>
                <span class="task-mini-stat">消息 ${messagesCount}</span>
              </div>
            </div>
            <div class="task-chrome-controls">
              <button type="button" data-task-chrome="scale" data-scale="compact" class="${scale === 'compact' ? 'active' : ''}" title="紧凑显示">小</button>
              <button type="button" data-task-chrome="scale" data-scale="normal" class="${scale === 'normal' ? 'active' : ''}" title="标准显示">中</button>
              <button type="button" data-task-chrome="scale" data-scale="large" class="${scale === 'large' ? 'active' : ''}" title="宽松显示">大</button>
              <button type="button" data-task-chrome="pin" class="${pinned ? 'active' : ''}" title="${pinned ? '已冻结在顶部，点击取消' : '点击冻结到顶部'}">${pinned ? '已冻结' : '冻结'}</button>
              <button type="button" data-task-chrome="toggle" class="task-toggle-detail">${collapsed ? '展开' : '收起'}</button>
            </div>
          </div>
          <div class="task-overview-top">
            <div class="task-overview-meta">
              ${chat.pinned ? '<span class="task-chip done">置顶任务</span>' : ''}
              <span class="task-chip ${deps.chatStatusClass?.(chat) || ''}">${escapeHtml(deps.chatStatusLabel?.(chat) || '进行中')}</span>
              <span class="task-chip">${escapeHtml(profile?.label || '未指定 Agent')}</span>
            </div>
          </div>
          <p class="task-overview-goal"><span class="task-overview-label">任务目标</span>${escapeHtml(chat.taskGoal || '暂无，建议补充明确目标。')}</p>
          <div class="task-router-strip ${recommendation ? 'ready' : 'idle'} ${recommendationBusy ? 'busy' : ''}">
            <div class="task-router-icon">${recommendationBusy ? '⌁' : recommendation ? '↯' : '◎'}</div>
            <div class="task-router-copy">
              <strong>${escapeHtml(recommendationBusy ? '正在分析任务分配' : recommendation ? `推荐：${recAgent?.label || recommendation.primaryAgentLabel || recommendation.primaryAgentId || 'Agent'}` : '智能任务调度')}</strong>
              <span>${escapeHtml(recommendationBusy ? 'Agent Hub 正在识别任务类型、上下文策略和最适合的 Agent。' : recommendation ? `${recommendation.taskTypeLabel || recommendation.taskType || '任务'} · ${deps.contextStrategyLabel?.(recommendation.contextStrategy) || recommendation.contextStrategy || '标准'} · 置信度 ${Math.round((recommendation.confidence || 0) * 100)}%` : '根据任务内容推荐 Agent、上下文策略和风险提示。')}</span>
              ${recommendation ? `<small>${escapeHtml(recReasons[0] || '推荐结果已生成，可查看详情后采用。')}</small>` : ''}
            </div>
            <div class="task-router-actions">
              ${recommendation ? `<button type="button" data-router-action="detail">理由</button><button type="button" data-router-action="accept" class="primary">采用</button>` : ''}
              <button type="button" data-router-action="recommend" ${recommendationBusy ? 'disabled' : ''}>${recommendation ? '重新分析' : '智能推荐'}</button>
            </div>
          </div>
          ${recommendation?.expanded ? `
          <div class="task-router-detail">
            <div>
              <strong>推荐理由</strong>
              <ul>${(recReasons.length ? recReasons : ['当前任务可由推荐 Agent 优先处理。']).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </div>
            <div>
              <strong>风险提示</strong>
              <ul>${(recWarnings.length ? recWarnings : ['暂无明显风险。']).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </div>
          </div>` : ''}
          <div class="task-spec-strip ${taskSpec ? 'ready' : 'idle'}">
            <div class="task-spec-copy">
              <strong>${escapeHtml(taskSpec ? `Task Spec · ${taskSpec.taskType || 'task'} / ${taskSpec.stage || 'draft'}` : 'Task Spec · 待生成')}</strong>
              <span>${escapeHtml(taskSpec ? (taskSpec.finalDeliverable || taskSpec.goal || '已生成任务目标和验收口径') : '把当前对话沉淀成“目标、约束、验收标准、相关文件”，用于后续 Agent 接力。')}</span>
            </div>
            <div class="task-spec-actions">
              <button type="button" data-task-spec-action="generate">${taskSpec ? '刷新任务书' : '生成任务书'}</button>
            </div>
          </div>
          <div class="task-workflow-strip" aria-label="任务流程阶段">
            <div class="task-workflow-head">
              <strong>Workflow</strong>
              <span>${escapeHtml(workflow?.current?.nextStep || '用阶段流转管理任务执行、复核、测试和归档。')}</span>
              <button type="button" class="task-workflow-action" data-workflow-action="stage-action">${escapeHtml(workflowActionLabel(currentStage))}</button>
              ${latestAction?.content ? `<button type="button" class="task-workflow-action primary" data-workflow-action="send-action">发送给推荐 Agent</button>` : ''}
            </div>
            <div class="task-workflow-rail">
              ${workflowStages.map(([id, label], index) => {
                const active = id === currentStage;
                const done = workflowStages.findIndex(item => item[0] === currentStage) > index;
                const disabled = !active && !allowedNext.has(id);
                return `<button type="button" data-workflow-stage="${id}" class="${active ? 'active' : ''} ${done ? 'done' : ''}" ${disabled ? 'disabled' : ''}><i></i>${escapeHtml(label)}</button>`;
              }).join('')}
            </div>
          </div>
          ${taskSpec && !collapsed ? `
          <div class="task-spec-detail">
            <div>
              <strong>目标</strong>
              <p>${escapeHtml(taskSpec.goal || chat.taskGoal || '暂无')}</p>
            </div>
            <div>
              <strong>验收标准</strong>
              <ul>${((taskSpec.acceptance || []).length ? taskSpec.acceptance : ['结果可直接用于下一步执行']).map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </div>
            ${(taskSpec.constraints || []).length ? `<div><strong>约束</strong><ul>${taskSpec.constraints.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : ''}
            ${(taskSpec.files || []).length ? `<div><strong>相关文件</strong><ul>${taskSpec.files.slice(0, 6).map(item => `<li>${escapeHtml(item.path || item.name || '')}</li>`).join('')}</ul></div>` : ''}
            ${taskSpec.riskNotes ? `<div><strong>风险</strong><p>${escapeHtml(taskSpec.riskNotes)}</p></div>` : ''}
          </div>` : ''}
          <div class="task-workspace-dock ${workspaceBound ? 'bound' : 'empty'}">
            <div class="task-workspace-icon">${workspaceBound ? '⌘' : '＋'}</div>
            <div class="task-workspace-copy">
              <span>Project Workspace</span>
              <strong>${escapeHtml(workspaceBound ? workspaceText : '按需绑定本地项目')}</strong>
              <small>${escapeHtml(workspaceBound ? chat.workspacePath : '做产品开发、代码分析、本地文件任务时建议绑定；普通对话可不绑定。')}</small>
            </div>
            <div class="task-workspace-actions">
              <select data-approval-policy title="Claude/OpenClaw 执行权限策略">
                <option value="auto" ${(chat.approvalPolicy || 'auto') === 'auto' ? 'selected' : ''}>自动判断</option>
                <option value="readonly" ${chat.approvalPolicy === 'readonly' ? 'selected' : ''}>只读</option>
                <option value="workspace-auto" ${chat.approvalPolicy === 'workspace-auto' ? 'selected' : ''}>工作区自动</option>
              </select>
              <button type="button" data-workspace-action="bind">${workspaceBound ? '更换' : '绑定项目'}</button>
              ${workspaceBound ? '<button type="button" data-workspace-action="clear">清除</button>' : ''}
            </div>
          </div>
          <p class="task-overview-next"><span class="task-overview-label">下一步</span>${escapeHtml(chat.nextStep || '等待你继续推动，或切换 Agent 接力。')}</p>
          <div class="task-overview-actions task-action-group" aria-label="任务操作">
            ${statusActions.map(([id, label]) => `<button data-task-action="${id}" class="${status === id || (id === 'done' && status === 'completed') ? 'primary' : ''}">${label}</button>`).join('')}
            <span class="task-action-separator"></span>
            <button data-task-action="rename">重命名</button>
            <button data-task-action="workspace">${chat.workspacePath ? '项目设置' : '绑定项目'}</button>
            <button data-task-action="pin">${chat.pinned ? '取消置顶' : '置顶'}</button>
            <button data-task-action="delete" class="danger">删除</button>
          </div>
        </div>
        <div class="task-overview-side task-metric-grid">
          <div class="task-overview-side-card task-metric-card">
            <strong>${artifactsCount}</strong>
            <p>附件资产</p>
          </div>
          <div class="task-overview-side-card task-metric-card">
            <strong>${chat.handoffSummary ? 'ON' : 'OFF'}</strong>
            <p>交接摘要</p>
          </div>
          <div class="task-overview-side-card task-metric-card">
            <strong>${messagesCount}</strong>
            <p>上下文消息</p>
          </div>
          <div class="task-overview-side-card task-metric-card wide">
            <strong>${escapeHtml(deps.formatRelativeTime?.(chat.updatedAt || chat.createdAt) || '刚刚更新')}</strong>
            <p>${escapeHtml(chat.handoffSummary ? '可直接切换 Agent 接力继续' : '切换 Agent 后会自动生成交接摘要')}</p>
          </div>
        </div>
      </div>
    `;
    root.querySelectorAll('[data-task-action]').forEach(btn => {
      btn.onclick = () => {
        const action = btn.dataset.taskAction;
        if(action === 'rename') return deps.renameTask?.(chat);
        if(action === 'workspace') return deps.bindWorkspacePrompt?.(chat);
        if(action === 'delete') return deps.deleteTask?.(chat);
        if(action === 'pin') return deps.toggleTaskPin?.(chat);
        return deps.setTaskStatus?.(chat, action);
      };
    });
    root.querySelectorAll('[data-task-chrome]').forEach(btn => {
      btn.onclick = event => {
        event.stopPropagation();
        if(btn.dataset.taskChrome === 'toggle') deps.setTaskChromeCollapsed?.(!collapsed);
        if(btn.dataset.taskChrome === 'scale') deps.setTaskChromeScale?.(btn.dataset.scale || 'compact');
        if(btn.dataset.taskChrome === 'pin') deps.setTaskChromePinned?.(!pinned);
      };
    });
    root.querySelectorAll('[data-workspace-action]').forEach(btn => {
      btn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        if(btn.dataset.workspaceAction === 'bind') return deps.bindWorkspacePrompt?.(chat);
        if(btn.dataset.workspaceAction === 'clear') return deps.clearWorkspace?.();
      };
    });
    root.querySelectorAll('[data-router-action]').forEach(btn => {
      btn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        const action = btn.dataset.routerAction;
        if(action === 'recommend') return deps.recommendAgentForTask?.(chat);
        if(action === 'accept') return deps.acceptTaskRecommendation?.(chat);
        if(action === 'detail') return deps.toggleTaskRecommendationDetail?.();
      };
    });
    root.querySelectorAll('[data-task-spec-action]').forEach(btn => {
      btn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        if(btn.dataset.taskSpecAction === 'generate') return deps.generateTaskSpecForTask?.(chat);
      };
    });
    root.querySelectorAll('[data-workflow-stage]').forEach(btn => {
      btn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        if(btn.disabled) return;
        return deps.transitionTaskStage?.(chat, btn.dataset.workflowStage);
      };
    });
    root.querySelectorAll('[data-workflow-action]').forEach(btn => {
      btn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        if(btn.dataset.workflowAction === 'stage-action') return deps.runWorkflowStageAction?.(chat, currentStage);
        if(btn.dataset.workflowAction === 'send-action') return deps.sendWorkflowActionToRecommendedAgent?.(chat);
      };
    });
    root.querySelectorAll('[data-approval-policy]').forEach(select => {
      select.onchange = event => {
        event.preventDefault();
        event.stopPropagation();
        deps.setApprovalPolicy?.(select.value || 'auto');
      };
      select.onclick = event => event.stopPropagation();
    });
    root.querySelector('.task-overview-compact-line')?.addEventListener('click', () => deps.setTaskChromeCollapsed?.(!collapsed));
  }

  function renderTaskDetailPanels(chat, deps = {}){
    const $ = deps.$;
    const escapeHtml = deps.escapeHtml || ((value) => String(value ?? ''));
    const root = $?.('taskDetailPanels');
    if(!root) return;
    const collapsed = deps.taskChromeCollapsed !== false;
    const scale = ['compact','normal','large'].includes(deps.taskChromeScale) ? deps.taskChromeScale : 'compact';
    root.classList.toggle('task-chrome-collapsed', collapsed);
    root.classList.remove('task-chrome-scale-compact', 'task-chrome-scale-normal', 'task-chrome-scale-large');
    root.classList.add(`task-chrome-scale-${scale}`);
    if(!chat){
      root.innerHTML = '';
      return;
    }
    const artifacts = (chat.artifacts || []).slice(-5).reverse();
    const handoffs = (deps.handoffRecords || [])
      .filter(item => item.session_id === chat.id)
      .slice(0, 6);
    if(!handoffs.length && chat.handoffSummary){
      handoffs.push({
        session_id: chat.id,
        from_agent_id: chat.handoffFromAgentId || '',
        to_agent_id: chat.handoffToAgentId || '',
        handoff_summary: chat.handoffSummary,
        created_at: chat.handoffAt || Date.now(),
        _fromLabel: chat.handoffFromLabel || '',
        _toLabel: chat.handoffToLabel || '',
      });
    }
    const runs = (deps.agentRuns || []).filter(run => run.session_id === chat.id).slice(0, 5);
    root.innerHTML = `
      <div class="task-detail-grid task-ops-grid">
        <section class="task-detail-card">
          <div class="task-detail-card-head"><span class="task-detail-icon">📎</span><h3>附件资产</h3><small>${artifacts.length}/5</small></div>
          ${artifacts.length ? `<div class="task-detail-list">${artifacts.map(item => `
            <div class="task-detail-item">
              <strong>${escapeHtml(item.name || 'attachment')}</strong>
              <span class="task-detail-meta-line">${escapeHtml(item.mime || item.kind || '-')} · ${item.includeInContext !== false ? '已固定到上下文' : '未固定'}</span>
              <small>${escapeHtml(deps.artifactPreviewText?.(item) || '')}</small>
            </div>
          `).join('')}</div>` : '<div class="task-detail-empty">当前任务还没有附件资产。</div>'}
        </section>
        <section class="task-detail-card handoff-timeline-card">
          <div class="task-detail-card-head"><span class="task-detail-icon">🔁</span><h3>Handoff Timeline</h3><small>${handoffs.length}/6</small></div>
          ${handoffs.length ? `<div class="handoff-timeline">${handoffs.map((item, index) => {
            const fromId = item.from_agent_id || chat.handoffFromAgentId || '';
            const toId = item.to_agent_id || chat.handoffToAgentId || '';
            const fromLabel = item._fromLabel || handoffAgentLabel(fromId, deps);
            const toLabel = item._toLabel || handoffAgentLabel(toId, deps);
            const summary = handoffSummaryText(item);
            return `
            <div class="handoff-timeline-item">
              <div class="handoff-flow">
                <span class="handoff-avatar">${escapeHtml(handoffAvatar(fromId))}</span>
                <i></i>
                <span class="handoff-avatar target">${escapeHtml(handoffAvatar(toId))}</span>
              </div>
              <div class="handoff-body">
                <div class="handoff-head">
                  <strong>${escapeHtml(fromLabel)} → ${escapeHtml(toLabel)}</strong>
                  <em>${escapeHtml(deps.formatRelativeTime?.(item.created_at || 0) || '')}</em>
                </div>
                <div class="handoff-meta">
                  <span>handoff #${index + 1}</span>
                  <span>${escapeHtml(item.created_at ? new Date(item.created_at).toLocaleString('zh-CN') : '最近一次交接')}</span>
                </div>
                <p>${escapeHtml(deps.truncateForCard?.(summary, 220) || summary || '暂无交接摘要。')}</p>
              </div>
            </div>`;
          }).join('')}</div>` : '<div class="task-detail-empty">当前任务还没有 Agent 交接记录；切换 Agent 后会自动生成上下文交接。</div>'}
        </section>
        <section class="task-detail-card task-run-timeline-card">
          <div class="task-detail-card-head"><span class="task-detail-icon">⌁</span><h3>Agent Run Timeline</h3><small>${runs.length}/5</small></div>
          ${runs.length ? `<div class="run-timeline">${runs.map((run, index) => `
            <div class="run-timeline-item ${runStatusClass(run.status)}">
              <div class="run-timeline-rail"><span>${escapeHtml(agentAvatarFromRun(run))}</span></div>
              <div class="run-timeline-body">
                <div class="run-timeline-head">
                  <strong>${escapeHtml(runAgentLabel(run, deps))}</strong>
                  <em>${escapeHtml(runStatusLabel(run.status || 'success'))}</em>
                </div>
                <div class="run-timeline-meta">
                  <span>${escapeHtml(run.adapter || 'adapter')}</span>
                  <span>${escapeHtml(deps.formatRelativeTime?.(run.created_at || 0) || '')}</span>
                  <span>${escapeHtml(deps.formatElapsed?.(run.latency_ms || 0) || '')}</span>
                  <span>#${index + 1}</span>
                </div>
                <p>${escapeHtml(run.output_summary || run.error || run.input_summary || '本次执行暂无摘要。')}</p>
                <div class="run-timeline-actions">
                  <button type="button" data-run-action="debug" data-run-id="${escapeHtml(run.id || '')}">详情</button>
                  <button type="button" data-run-action="retry" data-run-id="${escapeHtml(run.id || '')}">重试</button>
                  <button type="button" data-run-action="copy" data-run-id="${escapeHtml(run.id || '')}">复制上下文</button>
                </div>
              </div>
            </div>
          `).join('')}</div>` : '<div class="task-detail-empty">当前任务还没有执行时间线；发送消息后会自动记录 Agent、状态、耗时和摘要。</div>'}
        </section>
      </div>
    `;
    root.querySelectorAll('[data-run-action]').forEach(btn => {
      btn.onclick = event => {
        event.preventDefault();
        event.stopPropagation();
        const run = runs.find(item => String(item.id || '') === String(btn.dataset.runId || ''));
        if(!run) return;
        if(btn.dataset.runAction === 'debug') deps.openRunDebugPanel?.(run);
        if(btn.dataset.runAction === 'retry') deps.retryAgentRun?.(run);
        if(btn.dataset.runAction === 'copy') deps.copyRunContext?.(run);
      };
    });
  }

  window.AgentHubRenderTaskDetail = {
    renderTaskOverview,
    renderTaskDetailPanels,
  };
})();
