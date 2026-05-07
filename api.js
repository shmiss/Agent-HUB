(function(){
  async function jsonFetch(url, options = {}){
    const response = await fetch(url, options);
    const data = await response.json();
    if(!response.ok || data.ok === false){
      throw new Error((data.error || `HTTP ${response.status}`) + (data.detail ? '\n' + data.detail : ''));
    }
    return data;
  }

  function postJson(url, payload){
    return jsonFetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
  }

  window.AgentHubApi = {
    syncSessions(payload){ return postJson('/api/sessions/sync', payload); },
    sessions(){ return jsonFetch('/api/sessions'); },
    chat(payload){ return postJson('/api/chat', payload); },
    chatStream(payload, options = {}){
      return fetch('/api/chat-stream', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
        signal: options.signal
      });
    },
    config(){ return jsonFetch('/api/config'); },
    discoverAgents(){ return jsonFetch('/api/agents/discover'); },
    saveAgents(payload){ return postJson('/api/agents/save', payload); },
    handoff(payload){ return postJson('/api/sessions/handoff', payload); },
    handoffs(sessionId, limit = 50){ return jsonFetch(`/api/handoffs?session_id=${encodeURIComponent(sessionId || '')}&limit=${encodeURIComponent(limit)}`); },
    contextPackage(sessionId, agentId = '', strategy = ''){ return jsonFetch(`/api/context-package?session_id=${encodeURIComponent(sessionId || '')}&agent_id=${encodeURIComponent(agentId || '')}&strategy=${encodeURIComponent(strategy || '')}`); },
    taskRouterRecommend(payload){ return postJson('/api/task-router/recommend', payload); },
    taskRouterAccept(payload){ return postJson('/api/task-router/accept', payload); },
    taskSpec(sessionId){ return jsonFetch(`/api/task-spec?session_id=${encodeURIComponent(sessionId || '')}`); },
    generateTaskSpec(payload){ return postJson('/api/task-spec/generate', payload); },
    saveTaskSpec(payload){ return postJson('/api/task-spec/save', payload); },
    taskWorkflow(sessionId){ return jsonFetch(`/api/task-workflow?session_id=${encodeURIComponent(sessionId || '')}`); },
    transitionTaskStage(payload){ return postJson('/api/task-workflow/transition', payload); },
    workflowStageAction(payload){ return postJson('/api/task-workflow/stage-action', payload); },
    cancelRun(payload){ return postJson('/api/runs/cancel', payload); },
    inspectWorkspace(path){ return postJson('/api/workspace/inspect', {path}); },
    memories(workspacePath, q = '', limit = 30){ return jsonFetch(`/api/memories?workspace_path=${encodeURIComponent(workspacePath || '')}&q=${encodeURIComponent(q || '')}&limit=${encodeURIComponent(limit)}`); },
    saveMemory(payload){ return postJson('/api/memories/save', payload); },
    extractMemories(payload){ return postJson('/api/memories/extract', payload); },
    deleteMemory(id){ return postJson('/api/memories/delete', {id}); },
    health(headers = {}){ return jsonFetch('/api/health', {headers}); },
    models(headers = {}){ return jsonFetch('/api/models', {headers}); },
    parseAttachment(payload){ return postJson('/api/attachments/parse', payload); }
  };
})();
