(function(){
  function roleLabel(message, deps = {}){
    const role = typeof message === 'string' ? message : message?.role;
    if(role === 'user') return '你';
    if(role === 'assistant'){
      const profile = deps.getProfile?.(message?.agentId) || deps.getActiveProfile?.();
      if(profile?.type === 'hermes') return '🐴';
      if(profile?.type === 'openclaw') return '🦞';
      if(profile?.type === 'claude') return 'C';
      return 'AI';
    }
    if(role === 'progress') return '⟳';
    if(role === 'error') return '×';
    return 'S';
  }

  function displayContent(content){
    if(Array.isArray(content)) return content.map(p => p.type === 'text' ? p.text : '[图片附件]').join('\n');
    return String(content ?? '');
  }

  function formatElapsed(ms){
    return (ms / 1000).toFixed(1) + 's';
  }

  function activeVisibleMessages(chat){
    return (chat?.messages || []).filter(m => m.role !== 'system');
  }

  function renderHandoffBanner(chat, deps = {}){
    const $ = deps.$;
    if(!$) return;
    const root = $('handoffBanner');
    if(!root) return;
    if(!chat?.handoffSummary || chat.handoffDismissed){
      root.classList.add('hidden');
      return;
    }
    root.classList.remove('hidden');
    $('handoffBannerTitle').textContent = chat.handoffToLabel
      ? `最近一次 Agent 交接：${chat.handoffFromLabel || '上一 Agent'} → ${chat.handoffToLabel}`
      : '最近一次 Agent 交接';
    $('handoffBannerMeta').textContent = `${chat.handoffAt ? new Date(chat.handoffAt).toLocaleString('zh-CN') : '刚刚生成'} · 你现在可以直接继续任务，不需要重复描述上下文。`;
    $('handoffBannerContent').textContent = chat.handoffSummary || '';
  }

  function renderMessages(chat, deps = {}){
    const $ = deps.$;
    const escapeHtml = deps.escapeHtml || ((value) => String(value ?? ''));
    if(!$) return;
    $('chatTitle').textContent = chat?.title || '新的企业智能体会话';
    deps.renderTaskOverview?.();
    deps.renderTaskDetailPanels?.();
    renderHandoffBanner(chat, deps);
    const visible = activeVisibleMessages(chat);
    $('messages').innerHTML = '';
    $('emptyState').style.display = visible.length ? 'none' : 'block';
    $('messages').style.display = visible.length ? 'block' : 'none';
    for(const m of visible){
      const row = document.createElement('article');
      row.className = 'message ' + (m.role || 'assistant');
      if(m.role === 'progress'){
        const steps = (m.steps || []).map(step => {
          const state = step.state || 'pending';
          return `<div class="progress-step ${state}"><div class="progress-dot"></div><div>${escapeHtml(step.text)}</div></div>`;
        }).join('');
        row.innerHTML = `<div class="avatar">${roleLabel(m, deps)}</div><div class="progress-card"><div class="progress-title">${escapeHtml(m.title || `${deps.activeAgentName?.() || 'Agent'} 正在处理`)}</div><div class="progress-steps">${steps}</div><div class="progress-note">${escapeHtml(m.meta || '')}</div></div>`;
      } else {
        row.innerHTML = `<div class="avatar">${roleLabel(m, deps)}</div><div><div class="content">${escapeHtml(displayContent(m.content))}</div>${m.meta ? `<div class="meta">${escapeHtml(m.meta)}</div>` : ''}</div>`;
      }
      $('messages').appendChild(row);
    }
    $('messages').scrollTop = $('messages').scrollHeight;
  }

  function setBadge(type, text, deps = {}){
    const $ = deps.$;
    if(!$) return;
    const b = $('connBadge');
    if(!b) return;
    b.className = 'badge ' + type;
    b.textContent = text;
  }

  function setBusy(value, deps = {}){
    const $ = deps.$;
    if(!$) return;
    const btn = $('sendBtn');
    if(!btn) return;
    btn.disabled = value;
    btn.textContent = value ? '处理中…' : '发送';
    $('cancelComposerBtn')?.classList.toggle('hidden', !value);
  }

  function showBootNotice(show, deps = {}){
    const $ = deps.$;
    if(!$) return;
    $('bootNotice')?.classList.toggle('hidden', !show);
  }

  function updateRunStatus(progress, deps = {}){
    const $ = deps.$;
    if(!$) return;
    const wrap = $('runStatus');
    if(!wrap) return;
    if(!progress){
      wrap.classList.add('hidden');
      return;
    }
    wrap.classList.remove('hidden');
    $('cancelRunBtn')?.toggleAttribute('disabled', false);
    $('runStatusTitle').textContent = progress.title || `${deps.activeAgentName?.() || 'Agent'} 正在处理`;
    $('runStatusText').textContent = progress.statusText || '请求已发出，正在等待 Agent 返回。';
    $('runStatusElapsed').textContent = formatElapsed(Date.now() - progress.startedAt);
  }

  window.AgentHubRenderChat = {
    roleLabel,
    displayContent,
    formatElapsed,
    activeVisibleMessages,
    renderHandoffBanner,
    renderMessages,
    setBadge,
    setBusy,
    showBootNotice,
    updateRunStatus,
  };
})();
