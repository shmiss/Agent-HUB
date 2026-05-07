(function(){
  const MAX_LOCAL_CHATS = 40;
  const MAX_LOCAL_MESSAGES = 80;
  const MAX_LOCAL_ARTIFACTS = 40;

  function stripPersistedContent(content){
    if(!Array.isArray(content)) return content;
    return content.map(part => {
      if(part?.type !== 'image_url') return part;
      return {...part, image_url: {url: '[image omitted from localStorage]'}};
    });
  }

  function chatsForStorage(chats){
    return (chats || []).slice(0, MAX_LOCAL_CHATS).map(chat => ({
      ...chat,
      pinned: !!chat.pinned,
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
      artifacts: (chat.artifacts || []).slice(-MAX_LOCAL_ARTIFACTS),
      handoffFromAgentId: chat.handoffFromAgentId || '',
      handoffFromLabel: chat.handoffFromLabel || '',
      handoffToAgentId: chat.handoffToAgentId || '',
      handoffToLabel: chat.handoffToLabel || '',
      handoffAt: chat.handoffAt || 0,
      handoffDismissed: !!chat.handoffDismissed,
      messages: (chat.messages || []).slice(-MAX_LOCAL_MESSAGES).map(message => ({
        ...message,
        content: stripPersistedContent(message.content)
      }))
    }));
  }

  function settingsForStorage(settings, uiState = {}){
    return {
      ...settings,
      taskSort: uiState.taskSort,
      currentAgentOnly: !!uiState.currentAgentOnly,
      taskChromeCollapsed: uiState.taskChromeCollapsed !== false,
      taskChromeScale: uiState.taskChromeScale || 'compact',
      taskChromePinned: uiState.taskChromePinned !== false,
      modelRegistry: uiState.modelRegistry || settings.modelRegistry || {},
      agentProfiles: (settings.agentProfiles || []).map(profile => ({...profile, apiKey: ''})),
      discoveredAgents: (settings.discoveredAgents || []).map(profile => ({...profile, apiKey: ''}))
    };
  }

  function snapshot({settings, chats, activeId, taskSort, currentAgentOnly, taskChromeCollapsed, taskChromeScale, taskChromePinned, modelRegistry}){
    return {
      settings: settingsForStorage(settings, {taskSort, currentAgentOnly, taskChromeCollapsed, taskChromeScale, taskChromePinned, modelRegistry}),
      chats: chatsForStorage(chats),
      activeId
    };
  }

  function saveLocal(storeKey, payload){
    localStorage.setItem(storeKey, JSON.stringify(payload));
  }

  function loadLocal(storeKey){
    try{
      return JSON.parse(localStorage.getItem(storeKey) || '{}');
    }catch{
      return {};
    }
  }

  function sessionsForServer({settings, chats, activeId, taskSort, currentAgentOnly}){
    return {
      sessions: chatsForStorage(chats).map(chat => ({
        ...chat,
        activeAgentId: settings.activeAgentId,
        updatedAt: chat.updatedAt || Date.now()
      })),
      activeId,
      activeAgentId: settings.activeAgentId,
      taskSort,
      currentAgentOnly: !!currentAgentOnly
    };
  }

  window.AgentHubState = {
    stripPersistedContent,
    chatsForStorage,
    settingsForStorage,
    snapshot,
    saveLocal,
    loadLocal,
    sessionsForServer
  };
})();
