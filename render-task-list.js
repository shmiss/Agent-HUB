(function(){
  function matchesTaskFilter(chat, filter){
    if(filter === 'todo') return (chat?.taskStatus || 'todo') === 'todo';
    if(filter === 'active') return (chat?.taskStatus || 'active') === 'active';
    if(filter === 'blocked') return chat?.taskStatus === 'blocked';
    if(filter === 'done') return chat?.taskStatus === 'done' || chat?.taskStatus === 'completed';
    if(filter === 'handoff') return chat?.taskStatus === 'handoff' || !!chat?.handoffSummary;
    if(filter === 'assets') return Number((chat?.artifacts || []).length) > 0;
    return true;
  }

  function formatRelativeTime(ts){
    if(!ts) return '刚刚更新';
    const diff = Math.max(0, Date.now() - Number(ts));
    const minute = 60 * 1000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if(diff < minute) return '刚刚更新';
    if(diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
    if(diff < day) return `${Math.floor(diff / hour)} 小时前`;
    return `${Math.floor(diff / day)} 天前`;
  }

  function chatStatusLabel(chat){
    const status = chat?.taskStatus || 'todo';
    if(status === 'todo') return '待开始';
    if(status === 'blocked') return '阻塞';
    if(status === 'handoff') return '待交接';
    if(status === 'done' || status === 'completed') return '已完成';
    return '进行中';
  }

  function chatStatusClass(chat){
    const status = chat?.taskStatus || 'todo';
    if(status === 'todo') return 'idle';
    if(status === 'blocked') return 'bad';
    if(status === 'done' || status === 'completed') return 'done';
    if(status === 'handoff' || chat?.handoffSummary) return 'warm';
    return 'ok';
  }

  function truncateForCard(text, limit = 72){
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    if(!value) return '';
    return value.length <= limit ? value : value.slice(0, limit) + '…';
  }

  function chatPreview(chat, deps = {}){
    const visible = deps.activeVisibleMessages?.(chat) || [];
    const last = visible[visible.length - 1];
    if(!last) return chat?.taskGoal || '准备开始新的任务';
    return truncateForCard(deps.contentToText?.(last.content || '') || '', 72);
  }

  function sortChats(list, taskSort){
    const items = [...(list || [])];
    items.sort((a, b) => {
      if(!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
      if(taskSort === 'created_desc') return (b.createdAt || 0) - (a.createdAt || 0);
      if(taskSort === 'assets_desc') return Number((b.artifacts || []).length) - Number((a.artifacts || []).length) || (b.updatedAt || 0) - (a.updatedAt || 0);
      if(taskSort === 'title_asc') return String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN');
      return (b.updatedAt || 0) - (a.updatedAt || 0);
    });
    return items;
  }

  function renderTaskFilters(deps = {}){
    const $ = deps.$;
    const escapeHtml = deps.escapeHtml || ((value) => String(value ?? ''));
    const root = $?.('taskFilters');
    if(!root) return;
    const chats = deps.chats || [];
    const taskFilter = deps.taskFilter || 'all';
    const filters = [
      {id:'all', label:'全部'},
      {id:'todo', label:'待开始'},
      {id:'active', label:'进行中'},
      {id:'blocked', label:'阻塞'},
      {id:'handoff', label:'待交接'},
      {id:'done', label:'完成'},
      {id:'assets', label:'有附件'},
    ];
    root.innerHTML = filters.map(filter => {
      const count = chats.filter(chat => matchesTaskFilter(chat, filter.id)).length;
      return `<button class="task-filter-chip ${taskFilter === filter.id ? 'active' : ''}" data-filter="${filter.id}">${escapeHtml(filter.label)}<span>${count}</span></button>`;
    }).join('');
    root.querySelectorAll('[data-filter]').forEach(btn => {
      btn.onclick = () => {
        deps.setTaskFilter?.(btn.dataset.filter || 'all');
        deps.renderChatList?.();
        deps.renderTaskFilters?.();
      };
    });
  }

  function renderTaskToolbar(deps = {}){
    const $ = deps.$;
    const select = $?.('taskSort');
    const toggle = $?.('currentAgentOnly');
    if(select) select.value = deps.taskSort || 'updated_desc';
    if(toggle) toggle.classList.toggle('active', !!deps.currentAgentOnly);
  }

  function renderChatList(deps = {}){
    const $ = deps.$;
    const escapeHtml = deps.escapeHtml || ((value) => String(value ?? ''));
    const chatList = $?.('chatList');
    if(!chatList) return;
    const search = $?.('searchChat');
    const q = (search?.value || '').trim().toLowerCase();
    const chats = deps.chats || [];
    const settings = deps.settings || {};
    chatList.innerHTML = '';
    renderTaskFilters(deps);
    renderTaskToolbar(deps);
    const filtered = sortChats(chats, deps.taskSort).filter(c => {
      const queryHit = !q || String(c.title || '').toLowerCase().includes(q) || String(c.taskGoal || '').toLowerCase().includes(q);
      const filterHit = matchesTaskFilter(c, deps.taskFilter || 'all');
      const agentHit = !deps.currentAgentOnly || c.activeAgentId === settings.activeAgentId;
      return queryHit && filterHit && agentHit;
    });
    if(!filtered.length){
      chatList.innerHTML = '<div class="chat-list-empty">当前筛选条件下暂无任务。</div>';
      return;
    }
    filtered.forEach(c => {
      const profile = deps.getProfile?.(c.activeAgentId) || deps.getProfile?.(settings.activeAgentId);
      const b = document.createElement('button');
      b.className = 'chat-item task-card' + (c.id === deps.activeId ? ' active' : '');
      b.innerHTML = `
        <div class="task-card-head">
          <strong>${escapeHtml(c.title || '新的任务')}</strong>
          <span class="task-card-time">${escapeHtml(formatRelativeTime(c.updatedAt || c.createdAt))}</span>
        </div>
        <div class="task-card-meta">
          ${c.pinned ? '<span class="task-chip done">置顶</span>' : ''}
          <span class="task-chip ${chatStatusClass(c)}">${escapeHtml(chatStatusLabel(c))}</span>
          <span class="task-chip">${escapeHtml(profile?.label || '未指定 Agent')}</span>
        </div>
        <div class="task-card-preview">${escapeHtml(chatPreview(c, deps))}</div>
        <div class="task-card-foot">
          <span>附件 ${Number((c.artifacts || []).length)}</span>
          <span>交接 ${c.handoffSummary ? '已生成' : '无'}</span>
        </div>
        <div class="task-card-actions">
          <button class="task-action-button" data-rename="${escapeHtml(c.id)}">重命名</button>
          <button class="task-action-button" data-pin="${escapeHtml(c.id)}">${c.pinned ? '取消置顶' : '置顶'}</button>
        </div>
      `;
      b.onclick = () => {
        deps.setActiveId?.(c.id);
        deps.save?.();
        deps.renderAll?.();
      };
      b.querySelector('[data-rename]')?.addEventListener('click', event => {
        event.stopPropagation();
        const nextTitle = window.prompt('请输入新的会话名称：', c.title || '新的任务');
        if(nextTitle === null) return;
        const value = nextTitle.trim();
        if(!value) return;
        c.title = value.slice(0, 80);
        c.updatedAt = Date.now();
        deps.save?.();
        deps.renderAll?.();
      });
      b.querySelector('[data-pin]')?.addEventListener('click', event => {
        event.stopPropagation();
        c.pinned = !c.pinned;
        c.updatedAt = Date.now();
        deps.save?.();
        deps.renderChatList?.();
      });
      chatList.appendChild(b);
    });
  }

  window.AgentHubRenderTaskList = {
    matchesTaskFilter,
    formatRelativeTime,
    chatStatusLabel,
    chatStatusClass,
    truncateForCard,
    chatPreview,
    sortChats,
    renderTaskFilters,
    renderTaskToolbar,
    renderChatList,
  };
})();
