(function(){
  const defaultRunPhases = [
    { delay: 0, text: '请求已发出，正在连接 Agent' },
    { delay: 900, text: 'Agent 已接收任务，正在整理上下文' },
    { delay: 2200, text: '正在等待上游模型响应' },
    { delay: 5200, text: '任务耗时较长，Agent 仍在执行中' }
  ];

  function isOpenClawAdapter(profile){
    return profile?.adapter === 'openclaw-cli' || profile?.adapter === 'openclaw-gateway-rpc';
  }

  function isClaudeAdapter(profile){
    return profile?.adapter === 'claude-code-cli';
  }

  function runPhasesForProfile(profile, sessionId, fallback = defaultRunPhases){
    if(isClaudeAdapter(profile)){
      return [
        { delay: 0, text: '正在准备 Claude Code CLI print 模式' },
        { delay: 600, text: `已绑定 Agent Hub session：${sessionId}` },
        { delay: 1400, text: '正在执行 claude --bare -p --output-format json' },
        { delay: 4600, text: 'Claude Code 正在处理，等待最终回复' }
      ];
    }
    if(!isOpenClawAdapter(profile)) return fallback;
    if(profile?.adapter === 'openclaw-gateway-rpc') {
      return [
        { delay: 0, text: '正在准备 OpenClaw Gateway RPC 连接' },
        { delay: 500, text: `已绑定 OpenClaw session：${sessionId}` },
        { delay: 1200, text: `正在复用 Gateway WebSocket 调用 agent:${profile.agentId || 'main'}` },
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

  function agentProfileKey(profile){
    const p = profile || {};
    const adapter = String(p.adapter || '').trim().toLowerCase();
    const type = String(p.type || '').trim().toLowerCase();
    const base = String(p.baseUrl || p.binaryPath || '').trim().replace(/\/$/, '');
    const agentId = String(p.agentId || '').trim();
    const model = String(p.model || '').trim();
    return [type, adapter, base, agentId, model].join('|');
  }

  function normalizeProfile(profile, profileIdFactory){
    if(!profile) return null;
    const adapter = profile.adapter || (profile.type === 'openclaw' ? 'openclaw-gateway-rpc' : profile.type === 'claude' ? 'claude-code-cli' : 'openai-chat');
    let baseUrl = String(profile.baseUrl || '').trim();
    if(adapter === 'openai-chat') baseUrl = baseUrl.replace(/\/$/, '');
    return {
      id: profile.id || profileIdFactory(),
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
      apiServerEnabled: profile.apiServerEnabled,
      gatewayPort: profile.gatewayPort,
      gatewayAuthMode: profile.gatewayAuthMode || '',
      toolsProfile: profile.toolsProfile || '',
      toolDeny: Array.isArray(profile.toolDeny) ? profile.toolDeny : [],
      thinking: profile.thinking || '',
      version: profile.version || ''
    };
  }

  function uniqueProfiles(list, activeId = null, profileIdFactory){
    const result = [];
    const seen = new Map();
    const idMap = {};
    (list || []).map(item => normalizeProfile(item, profileIdFactory)).filter(Boolean).forEach(profile => {
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
        ['notes','configSource','binaryPath','status','version'].forEach(field => {
          if(!kept[field] && profile[field]) kept[field] = profile[field];
        });
      }
    });
    return {profiles: result, idMap};
  }

  function adapterLabel(adapter){
    if(adapter === 'openclaw-gateway-rpc') return 'OpenClaw Gateway RPC';
    if(adapter === 'openclaw-gateway') return 'OpenClaw Gateway';
    if(adapter === 'openclaw-cli') return 'OpenClaw CLI';
    if(adapter === 'claude-code-cli') return 'Claude Code CLI';
    return 'OpenAI-compatible';
  }

  function typeLabel(type){
    return type === 'hermes' ? 'Hermes' : type === 'openclaw' ? 'OpenClaw' : type === 'claude' ? 'Claude Code' : 'Custom';
  }

  function agentCapabilityCard(profile){
    const p = profile || {};
    const adapter = p.adapter || '';
    const type = p.type || 'custom';
    const isRpc = adapter === 'openclaw-gateway-rpc';
    const isCli = adapter === 'openclaw-cli';
    const hasFullTools = !p.toolsProfile || p.toolsProfile === 'full' || p.agentId === 'main';
    if(type === 'claude' || adapter === 'claude-code-cli'){
      return {
        label: '代码理解 / 仓库分析',
        role: '代码型 Agent',
        bestFor: '仓库理解、代码解释、改造建议、长上下文代码任务',
        strengths: ['代码语义强', '适合仓库级分析', '可承接工程任务'],
        cautions: ['CLI 调用通常非流式', '复杂任务耗时较长', '涉及文件修改需人工确认'],
        badges: ['Code', 'Local CLI', 'Long Task'],
        risk: 'medium',
        riskLabel: '中风险：可能触达本地项目'
      };
    }
    if(type === 'openclaw' || isOpenClawAdapter(p)){
      return {
        label: '本地工具执行',
        role: isRpc ? '本地执行型 Agent · RPC' : isCli ? '本地执行型 Agent · CLI' : '本地执行型 Agent',
        bestFor: hasFullTools ? '本机任务、工具调用、文件处理、需要接近 TUI 能力的执行任务' : '轻量、安全、受限的本机任务',
        strengths: [isRpc ? '连接可复用' : '兼容 CLI', hasFullTools ? '工具能力较完整' : '工具权限更收敛', '适合接力执行'],
        cautions: ['复杂工具链可能运行较久', '本地文件/命令操作需确认', '建议保留任务过程记录'],
        badges: [isRpc ? 'RPC' : 'CLI', hasFullTools ? 'Full Tools' : 'Restricted', 'Local'],
        risk: hasFullTools ? 'high' : 'medium',
        riskLabel: hasFullTools ? '高风险：具备较强本地执行能力' : '中风险：本地能力受限'
      };
    }
    if(type === 'hermes' || adapter === 'openai-chat'){
      return {
        label: p.supportsVision ? '通用对话 / 多模态' : '通用对话',
        role: '通用规划型 Agent',
        bestFor: '方案分析、任务拆解、写作、总结、上下文整理与交接',
        strengths: ['响应快', '适合前期分析', p.supportsVision ? '支持多模态输入' : '上下文整理稳定'],
        cautions: ['不直接等同本地执行器', '需要执行本机任务时建议交接给执行型 Agent'],
        badges: [p.supportsVision ? 'Vision' : 'Chat', 'Planner', 'Fast'],
        risk: 'low',
        riskLabel: '低风险：以分析和对话为主'
      };
    }
    return {
      label: '自定义 Agent',
      role: '自定义接入',
      bestFor: '由用户配置决定',
      strengths: ['可扩展', '可接入本地或 OpenAI-compatible 服务'],
      cautions: ['能力和风险取决于具体配置', '建议先完成健康检查'],
      badges: ['Custom'],
      risk: 'unknown',
      riskLabel: '未知风险：请核验配置'
    };
  }

  function agentCapabilityLabel(profile){
    return agentCapabilityCard(profile).label;
  }

  function sourceLabel(source){
    return source === 'detected' ? '自动发现' : source === 'imported' ? '导入' : source === 'generated' ? '生成' : source === 'local-cache' ? '缓存' : '手动';
  }

  window.AgentHubAgents = {
    isOpenClawAdapter,
    isClaudeAdapter,
    runPhasesForProfile,
    agentProfileKey,
    normalizeProfile,
    uniqueProfiles,
    adapterLabel,
    typeLabel,
    agentCapabilityLabel,
    agentCapabilityCard,
    sourceLabel
  };
})();
