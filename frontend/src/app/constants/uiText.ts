export const uiText = {
  appWelcome: '你好，我是 OpenResearch 科研助手。直接说出论文或方法名称，我会检索资料并规划实验；也可提供仓库链接或上传论文与数据。只有关键材料无法确定时才会向你询问，高级实验设置是可选入口。',
  planGenerated: '我已经生成执行计划，右侧会按依赖顺序展示任务节点。',
  backendError: '请求失败，请检查 Python API、连接设置及模型配置。',
  graphTitle: '多智能体执行流程',
  graphHint: '等待生成执行计划',
  runAll: '运行计划',
  planStartMessage: '正在启动整张拓扑图执行，并订阅计划级状态流。',
  planStartFailedMessage: '启动整图执行失败，请确认后端计划接口已经启动。',
  noPlanMessage: '当前前端已经切换到 plan_graph 主流程，无法回退旧任务列表执行链路。请重新生成计划后再执行。',
} as const;
