export const uiText = {
  appWelcome: '你好，我是 OpenResearch 科研助手。直接描述复现目标、提供仓库链接或上传论文与数据，我会自动规划实验；缺少关键材料时会在聊天中向你询问。也可通过右上角高级实验设置自行指定参数。',
  planGenerated: '我已经生成执行计划，右侧会按依赖顺序展示任务节点。',
  backendError: '请求失败，请检查 Python API、连接设置及模型配置。',
  graphTitle: '多智能体执行流程',
  graphHint: '等待生成执行计划',
  runAll: '运行计划',
  planStartMessage: '正在启动整张拓扑图执行，并订阅计划级状态流。',
  planStartFailedMessage: '启动整图执行失败，请确认后端计划接口已经启动。',
  noPlanMessage: '当前前端已经切换到 plan_graph 主流程，无法回退旧任务列表执行链路。请重新生成计划后再执行。',
} as const;
