export const agentNames: Record<string, string> = {
  chat_agent: 'Chat Agent', librarian_agent: 'Librarian Agent', coder_agent: 'Coder Agent',
  sandbox_agent: 'Sandbox Agent', data_agent: 'Data Agent', research_agent: 'Research Agent',
  research_coding_agent: 'Research Coding Agent', general_agent: 'General Agent',
};

export const intentNames: Record<string, string> = {
  Paper_Reproduction: '论文复现', Framework_Comparison: '框架对比', Code_Execution: '代码执行',
  General: '通用任务', Data_Analysis: '数据分析', Paper_Analysis: '论文阅读', Benchmark: '数据评测',
  Result_Review: '结果复核', Knowledge_Research: '资料研究', Result_Gap_Repair: '结果修复', General_Response: '通用回答',
};

export const statusNames: Record<string, string> = {
  pending: '等待执行', ready: '准备执行', in_progress: '运行中', completed: '已完成',
  failed: '执行失败', blocked: '已阻塞', cancelled: '已取消', canceled: '已取消', awaiting_approval: '等待审批',
};
