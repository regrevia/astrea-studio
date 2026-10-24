import { z } from 'zod'
import { AssistantToolDefinition } from './studio-assistant.client'

/** 指挥台允许模型调用的全部工具。模型只会拿到这几个名字，白名单即权限。 */
export enum AssistantToolName {
  RESEARCH_WEB = 'research_web',
  LIST_PLATFORMS = 'list_platforms',
  LIST_PROJECTS = 'list_projects',
  CREATE_PROJECT = 'create_project',
  CREATE_RUN = 'create_run',
  GET_RUN = 'get_run',
  LIST_RUNS = 'list_runs',
  REVISE_RUN = 'revise_run',
  LIST_EVENTS = 'list_events',
  PLAN_PUBLICATION = 'plan_publication',
}

const runIdSchema = z.string().regex(/^[a-f0-9]{24}$/i, '任务 ID 是 24 位十六进制字符串')

export const researchWebArgsSchema = z.object({
  url: z.string().min(1).describe('公开网页链接，http/https'),
})

export const listPlatformsArgsSchema = z.object({})

export const listProjectsArgsSchema = z.object({})

export const createProjectArgsSchema = z.object({
  name: z.string().min(1).max(40).describe('项目名'),
})

export const createRunArgsSchema = z.object({
  title: z.string().min(1).max(60).describe('任务标题'),
  script: z.array(z.string().min(1)).min(1).describe('分句文案，一句一条'),
  materials: z.array(z.string().min(1)).describe('素材路径或 URL，必须是用户给过的真实素材'),
  assetBase: z.string().optional().describe('素材是相对路径时的根目录'),
  canvas: z.enum(['portrait', 'landscape', 'square']).default('portrait').describe('画幅朝向'),
  platforms: z.array(z.string().min(1)).optional().describe('目标平台标识'),
  bgm: z.object({
    src: z.string().min(1),
    volume: z.number().min(0).max(1).optional(),
  }).optional().describe('背景音乐'),
  clipSeconds: z.number().min(1).max(15).optional().describe('每个镜头的秒数，默认 3'),
})

export const getRunArgsSchema = z.object({
  runId: runIdSchema,
})

export const listRunsArgsSchema = z.object({
  limit: z.number().int().min(1).max(10).default(5).describe('最多返回几条，默认 5'),
})

export const reviseRunArgsSchema = z.object({
  runId: runIdSchema,
  patch: z.object({
    brief: z.string().optional(),
    script: z.array(z.string().min(1)).optional(),
    materials: z.array(z.string().min(1)).optional(),
    canvas: z.enum(['portrait', 'landscape', 'square']).optional(),
    platforms: z.array(z.string().min(1)).optional(),
    bgm: z.object({
      src: z.string().min(1),
      volume: z.number().min(0).max(1).optional(),
    }).optional(),
    assetBase: z.string().optional(),
    clipSeconds: z.number().min(1).max(15).optional(),
  }).describe('只写要改的字段，其余沿用上一版'),
})

export const listEventsArgsSchema = z.object({
  runId: runIdSchema,
})

export const planPublicationArgsSchema = z.object({
  runId: runIdSchema,
  platforms: z.array(z.string().min(1)).min(1).describe('要发布到的平台标识'),
})

/** 给模型的工具清单。描述里写清「什么时候用」和「需要用户先提供什么」。 */
export const ASSISTANT_TOOLS: AssistantToolDefinition[] = [
  {
    type: 'function',
    function: {
      name: AssistantToolName.RESEARCH_WEB,
      description: '抓取一个公开网页的正文，用于查品牌/热点资料。只支持 http/https 公网地址，返回压缩后的正文节选。',
      parameters: {
        type: 'object',
        properties: {
          url: { type: 'string', description: '公开网页链接' },
        },
        required: ['url'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.LIST_PLATFORMS,
      description: '查询当前本机已就绪的发布平台账号、可用音色和 worker 支持的能力。回答「能发哪里/有没有声音」之前先调它。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.LIST_PROJECTS,
      description: '列出用户的 Production 项目。创建任务前如果不知道项目，先调它。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.CREATE_PROJECT,
      description: '新建一个 Production 项目。',
      parameters: {
        type: 'object',
        properties: {
          name: { type: 'string', description: '项目名' },
        },
        required: ['name'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.CREATE_RUN,
      description: '创建一条渲染任务并入队。materials 与 script 必须都是用户真实提供过的素材和文案，缺任何一项都不要调用。',
      parameters: {
        type: 'object',
        properties: {
          title: { type: 'string', description: '任务标题' },
          script: { type: 'array', items: { type: 'string' }, description: '分句文案，一句一条' },
          materials: { type: 'array', items: { type: 'string' }, description: '素材路径或 URL' },
          assetBase: { type: 'string', description: '素材是相对路径时的根目录' },
          canvas: { type: 'string', enum: ['portrait', 'landscape', 'square'], description: '画幅朝向，默认竖屏' },
          platforms: { type: 'array', items: { type: 'string' }, description: '目标平台标识' },
          bgm: {
            type: 'object',
            properties: {
              src: { type: 'string' },
              volume: { type: 'number' },
            },
            required: ['src'],
            additionalProperties: false,
            description: '背景音乐',
          },
          clipSeconds: { type: 'number', description: '每个镜头秒数，默认 3' },
        },
        required: ['title', 'script', 'materials'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.GET_RUN,
      description: '查询单条任务的进度、阶段、失败原因和成片产物。用户问「渲染到哪了/好了吗」时用它。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID' },
        },
        required: ['runId'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.LIST_RUNS,
      description: '列出最近的渲染任务及其状态。用户没给任务 ID 时用它找到最新那条。',
      parameters: {
        type: 'object',
        properties: {
          limit: { type: 'number', description: '最多返回几条，默认 5' },
        },
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.REVISE_RUN,
      description: '在上一版基础上改一处再重渲染（例如把字幕调大改文案、换画幅）。patch 只写要改的字段。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID' },
          patch: {
            type: 'object',
            description: '要覆盖的字段：brief/script/materials/canvas/platforms/bgm/assetBase/clipSeconds',
            additionalProperties: true,
          },
        },
        required: ['runId', 'patch'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.LIST_EVENTS,
      description: '读取一条任务的事件流水，用来说明为什么失败或卡住。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID' },
        },
        required: ['runId'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.PLAN_PUBLICATION,
      description: '登记发布意向（只记账，不会真的提交）。必须在任务已渲染成功之后调用，最终仍需用户在面板人工确认。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID' },
          platforms: { type: 'array', items: { type: 'string' }, description: '要发布到的平台标识' },
        },
        required: ['runId', 'platforms'],
        additionalProperties: false,
      },
    },
  },
]
