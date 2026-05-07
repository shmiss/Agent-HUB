(function(){
  const DEFAULTS = {
    maxContextMessages: 8,
    maxContextChars: 6000,
    maxSingleMessageChars: 3000,
    systemContentMaxChars: 4200,
  };

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

  function trimMessageContent(content, maxChars = DEFAULTS.maxSingleMessageChars){
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

  function artifactPreviewText(artifact){
    if(!artifact) return '';
    if(artifact.kind === 'image') return '图片附件';
    if(artifact.text) return String(artifact.text).replace(/\s+/g, ' ').slice(0, 120);
    if(artifact.kind === 'file') return `文件附件 · ${artifact.mime || 'binary'}`;
    return artifact.mime || artifact.kind || '附件';
  }

  function artifactContextBlock(chat){
    const artifacts = (chat?.artifacts || [])
      .filter(item => item.includeInContext !== false)
      .slice(-6);
    if(!artifacts.length) return '';
    const lines = artifacts.map((artifact, index) => {
      const meta = [artifact.kind, artifact.mime].filter(Boolean).join(' · ');
      const preview = artifactPreviewText(artifact);
      return `${index + 1}. ${artifact.name || 'attachment'}${meta ? ` [${meta}]` : ''}${preview ? `\n   摘要: ${preview}` : ''}`;
    });
    return `[Agent Hub 会话附件资产]\n${lines.join('\n')}`;
  }

  function messagesForApi(chat, deps = {}){
    const options = {...DEFAULTS, ...(deps.options || {})};
    const activeAgentName = deps.activeAgentName || (() => 'Agent');
    const all = (chat?.messages || []).filter(m => m.role === 'system' || m.role === 'user' || m.role === 'assistant');
    const system = all.find(m => m.role === 'system');
    const conversational = all.filter(m => m.role !== 'system');
    const selected = [];
    const contextBlocks = [];
    if(chat?.summary) contextBlocks.push(`[Agent Hub 会话摘要]\n${chat.summary}`);
    if(chat?.handoffSummary) contextBlocks.push(`[Agent Hub Agent 交接摘要]\n${chat.handoffSummary}`);
    if(chat?.taskGoal) contextBlocks.push(`[Agent Hub 当前任务目标]\n${chat.taskGoal}`);
    if(chat?.nextStep) contextBlocks.push(`[Agent Hub 下一步建议]\n${chat.nextStep}`);
    const artifactBlock = artifactContextBlock(chat);
    if(artifactBlock) contextBlocks.push(artifactBlock);
    const systemContent = [
      system ? contentToText(system.content) : `你是 ${activeAgentName()} 企业智能体。请用中文、结构化、专业但简洁的方式回答。`,
      ...contextBlocks
    ].filter(Boolean).join('\n\n');
    let totalChars = systemContent.length;

    for(let i = conversational.length - 1; i >= 0; i -= 1){
      const msg = conversational[i];
      const textLength = contentToText(msg.content).length;
      if(selected.length >= options.maxContextMessages) break;
      if(selected.length > 0 && totalChars + textLength > options.maxContextChars) break;
      selected.unshift({ role: msg.role, content: trimMessageContent(msg.content, options.maxSingleMessageChars) });
      totalChars += textLength;
    }

    return [
      { role: 'system', content: trimMessageContent(systemContent, options.systemContentMaxChars) },
      ...selected,
    ];
  }

  function summarizeApiContext(messages){
    const conversational = messages.filter(m => m.role !== 'system');
    const chars = messages.reduce((sum, msg) => sum + contentToText(msg.content).length, 0);
    return `${conversational.length} 条上下文 · ~${chars} chars`;
  }

  function currentContextStats(chat, deps = {}){
    const apiMessages = messagesForApi(chat, deps);
    const chars = apiMessages.reduce((sum, msg) => sum + contentToText(msg.content).length, 0);
    return {
      messages: apiMessages.length,
      conversational: apiMessages.filter(m => m.role !== 'system').length,
      chars,
      hasSummary: !!chat?.summary,
      hasHandoff: !!chat?.handoffSummary,
      artifacts: (chat?.artifacts || []).length,
      pinnedArtifacts: (chat?.artifacts || []).filter(item => item.includeInContext !== false).length,
    };
  }

  window.AgentHubContext = {
    contentToText,
    trimMessageContent,
    artifactPreviewText,
    artifactContextBlock,
    messagesForApi,
    summarizeApiContext,
    currentContextStats,
  };
})();
