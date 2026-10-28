import { z } from 'zod'
import { AssistantToolDefinition } from './studio-assistant.client'
import { StudioJobKind } from './studio-jobs.client'

/** 指挥台允许模型调用的全部工具。模型只会拿到这几个名字，白名单即权限。 */
export enum AssistantToolName {
  RESEARCH_WEB = 'research_web',
  LIST_PLATFORMS = 'list_platforms',
  LIST_STUDIO_ASSETS = 'list_studio_assets',
  LIST_STUDIO_DELIVERABLES = 'list_studio_deliverables',
  LIST_PROJECTS = 'list_projects',
  CREATE_PROJECT = 'create_project',
  CREATE_RUN = 'create_run',
  CREATE_RUN_BATCH = 'create_run_batch',
  GET_RUN = 'get_run',
  LIST_RUNS = 'list_runs',
  REVISE_RUN = 'revise_run',
  LIST_EVENTS = 'list_events',
  PLAN_PUBLICATION = 'plan_publication',
  SUBMIT_RENDER = 'submit_render',
  GET_RENDER_JOB = 'get_render_job',
  CANCEL_RENDER_JOB = 'cancel_render_job',
  CREATE_JIANYING_DRAFT = 'create_jianying_draft',
  START_JIANYING_EXPORT = 'start_jianying_export',
  OPEN_JIANYING_DRAFT = 'open_jianying_draft',
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
  materials: z.array(z.string().min(1)).default([]).describe('素材路径或 URL；不用用户素材时省略，卡面、配音、立绘由流水线自己出'),
  topic: z.string().min(1).max(40).optional().describe('顶栏主题，省略就用标题'),
  hook: z.string().min(1).max(40).optional().describe('首屏钩子，一行一个反差，省略就用第一句文案'),
  points: z.array(z.string().min(1)).optional().describe('要点，每项「标题::卡面正文::口播」；卡面正文写短句，口播写完整说法，省略就按文案粗排'),
  cta: z.string().min(1).max(40).optional().describe('收尾一句动作，省略就用最后一句文案'),
  voice: z.string().min(1).optional().describe('音色标识，省略用默认音色'),
  assetBase: z.string().optional().describe('素材是相对路径时的根目录'),
  canvas: z.enum(['portrait', 'landscape', 'square']).default('portrait').describe('画幅朝向'),
  platforms: z.array(z.string().min(1)).optional().describe('目标平台标识'),
  bgm: z.object({
    src: z.string().min(1),
    volume: z.number().min(0).max(1).optional(),
    bpm: z.number().min(1).max(300).optional().describe('心率：给了就按 60/bpm 自动排拍点'),
    offset: z.number().min(0).optional().describe('第一拍在音频里的秒数'),
    beats: z.array(z.number().min(0)).optional().describe('手动拍点秒数，严格递增；与 bpm 二选一'),
    pulse: z.object({
      amount: z.number().min(0).max(0.4).optional().describe('拍点把画面顶大多少，默认 0.045'),
      decay: z.number().min(0.03).max(1).optional().describe('顶起后回落秒数，默认 0.16'),
    }).optional().describe('卡点脉冲：给了才在拍点上推镜头'),
  }).optional().describe('背景音乐与卡点；src 为音频文件绝对路径'),
  clipSeconds: z.number().min(1).max(15).optional().describe('每个镜头的秒数，默认 3'),
})

/** 一次出多条：同一套文案/素材，标题自动带 1/N 后缀，用来做同题多版 A/B。 */
export const createRunBatchArgsSchema = createRunArgsSchema.extend({
  count: z.number().int().min(2).max(10).describe('一次出几条，2~10'),
  variants: z.array(z.enum(['mirror', 'opening', 'hook'])).optional()
    .describe('逐条怎么不同：mirror 换运镜、opening 换开场、hook 换开头字幕；不传就三维全用，第 1 条永远是原稿'),
})

export const getRunArgsSchema = z.object({
  runId: runIdSchema,
})

export const listRunsArgsSchema = z.object({
  limit: z.number().int().min(1).max(10).default(5).describe('最多返回几条，默认 5'),
  projectId: z.string().min(1).optional().describe('只看某个项目的任务，省略就是全部项目'),
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
      bpm: z.number().min(1).max(300).optional(),
      offset: z.number().min(0).optional(),
      beats: z.array(z.number().min(0)).optional(),
      pulse: z.object({
        amount: z.number().min(0).max(0.4).optional(),
        decay: z.number().min(0.03).max(1).optional(),
      }).optional(),
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

export const submitRenderArgsSchema = z.object({
  kind: z.enum(StudioJobKind).describe('出片引擎'),
  payload: z.record(z.string(), z.unknown()).describe('引擎参数；specPath 或 spec 必须给一个，slug/out 可选'),
  label: z.string().min(1).max(40).optional().describe('给用户看的一句话动作名，省略就用引擎名'),
})

export const getRenderJobArgsSchema = z.object({
  jobId: z.string().min(1).describe('出片任务号，submit_render 返回的那个'),
})

export const cancelRenderJobArgsSchema = z.object({
  jobId: z.string().min(1).describe('要取消的出片任务号'),
})

export const listStudioDeliverablesArgsSchema = z.object({
  limit: z.number().int().min(1).max(20).default(10).describe('最多返回几条成片，默认 10'),
})

export const createJianyingDraftArgsSchema = z.object({
  runId: runIdSchema,
  title: z.string().min(1).max(40).optional().describe('工程名，省略就用任务标题'),
})

export const startJianyingExportArgsSchema = z.object({
  runId: runIdSchema,
  allowGui: z.boolean().optional().describe('只有用户明确让你在这台 Mac 上驱动剪映导出时才传 true'),
  claimPath: z.string().min(1).optional()
    .describe('用户已经自己点过导出了：给他导出的成片路径，只做体检入库，不再动剪映'),
})

export const openJianyingDraftArgsSchema = z.object({
  runId: runIdSchema,
  draftPath: z.string().min(1).optional()
    .describe('草稿目录；省略就用任务上次落的草稿'),
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
      description: '查询当前本机已就绪的发布平台账号、可用音色、素材包（可用 pack:<包名>/<名字> 引用）和 worker 支持的能力。回答「能发哪里/有没有声音/有什么素材」之前先调它。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.LIST_STUDIO_ASSETS,
      description: '列出本机渲染素材的落盘情况：素材根目录、规格说明、已备好的镜头、素材包和成片数量与最近几部。要判断「有没有可用的素材/已经出过什么」时用它。',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.LIST_STUDIO_DELIVERABLES,
      description: '列出已交付的成片清单（文件名、绝对路径、大小、时间，按时间倒序）。用户问「最近的成片在哪/给我打开」时用它。',
      parameters: {
        type: 'object',
        properties: {
          limit: { type: 'number', description: '最多返回几条，默认 10' },
        },
        additionalProperties: false,
      },
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
      description: '创建一条渲染任务并入队。script 是从模型自己或用户写的分句文案；materials 可省，省了就用卡面+鲸鱼娘立绘+自动配音出片，只有用户指定自己的图片/视频时才传。',
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
              src: { type: 'string', description: '音频文件绝对路径' },
              volume: { type: 'number', description: '0~1，默认 0.35' },
              bpm: { type: 'number', description: '心率；给了就按 60/bpm 排拍点' },
              offset: { type: 'number', description: '第一拍在音频里的秒数' },
              beats: { type: 'array', items: { type: 'number' }, description: '手动拍点秒数，严格递增' },
              pulse: {
                type: 'object',
                properties: {
                  amount: { type: 'number', description: '拍点推镜头幅度，默认 0.045' },
                  decay: { type: 'number', description: '拍点回落秒数，默认 0.16' },
                },
                additionalProperties: false,
              },
            },
            required: ['src'],
            additionalProperties: false,
            description: '背景音乐与卡点；bpm/beats 决定拍点，pulse 决定拍点上是否推镜头',
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
      name: AssistantToolName.CREATE_RUN_BATCH,
      description: '同一条文案/素材一次排 N 条任务，标题自动加 1/N 后缀，用来做同题多版挑更好的那条。参数和 create_run 一致，只多一个 count。',
      parameters: {
        type: 'object',
        properties: {
          title: { type: 'string', description: '任务标题，会带上 1/N 后缀' },
          script: { type: 'array', items: { type: 'string' }, description: '分句文案，一句一条' },
          materials: { type: 'array', items: { type: 'string' }, description: '素材路径或 URL' },
          assetBase: { type: 'string', description: '素材是相对路径时的根目录' },
          canvas: { type: 'string', enum: ['portrait', 'landscape', 'square'], description: '画幅朝向，默认竖屏' },
          platforms: { type: 'array', items: { type: 'string' }, description: '目标平台标识' },
          bgm: {
            type: 'object',
            properties: {
              src: { type: 'string', description: '音频文件绝对路径' },
              volume: { type: 'number', description: '0~1，默认 0.35' },
              bpm: { type: 'number', description: '心率；给了就按 60/bpm 排拍点' },
              offset: { type: 'number', description: '第一拍在音频里的秒数' },
              beats: { type: 'array', items: { type: 'number' }, description: '手动拍点秒数，严格递增' },
              pulse: {
                type: 'object',
                properties: {
                  amount: { type: 'number', description: '拍点推镜头幅度，默认 0.045' },
                  decay: { type: 'number', description: '拍点回落秒数，默认 0.16' },
                },
                additionalProperties: false,
              },
            },
            required: ['src'],
            additionalProperties: false,
            description: '背景音乐与卡点；bpm/beats 决定拍点，pulse 决定拍点上是否推镜头',
          },
          clipSeconds: { type: 'number', description: '每个镜头秒数，默认 3' },
          count: { type: 'number', description: '一次出几条，2~10' },
        },
        required: ['title', 'script', 'materials', 'count'],
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
          projectId: { type: 'string', description: '只看某个项目的任务，省略就是全部项目' },
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
            description: '要覆盖的字段：brief/script/materials/canvas/platforms/bgm（含 bpm/beats/pulse 卡点）/assetBase/clipSeconds',
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
      name: AssistantToolName.SUBMIT_RENDER,
      description: '在用户本机的渲染队列下单出片。异步：下单当场返回任务号，之后用 get_render_job 查进度。spec 或 specPath 必须给一个——timeline 要完整分镜（canvas/video/text/overlay/audio），motion/slides/jianying 按各自规格写。',
      parameters: {
        type: 'object',
        properties: {
          kind: { type: 'string', enum: Object.values(StudioJobKind), description: '出片引擎' },
          payload: {
            type: 'object',
            description: '引擎参数。specPath：已有规格文件的绝对路径；spec：直接把规格对象写进来（队列会落盘）；slug：产物文件名前缀；out：成片绝对路径，省略就落到队列交付目录',
            additionalProperties: true,
          },
          label: { type: 'string', description: '给用户看的一句话动作名' },
        },
        required: ['kind', 'payload'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.GET_RENDER_JOB,
      description: '查一条出片任务的进度、失败原因和成片路径。成功时 result.files 是成片绝对路径，可直接给用户。',
      parameters: {
        type: 'object',
        properties: {
          jobId: { type: 'string', description: '出片任务号' },
        },
        required: ['jobId'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.CANCEL_RENDER_JOB,
      description: '取消排队中或正在跑的出片任务。已经结束的任务不会被回滚，队列会如实返回当前状态。',
      parameters: {
        type: 'object',
        properties: {
          jobId: { type: 'string', description: '要取消的出片任务号' },
        },
        required: ['jobId'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.CREATE_JIANYING_DRAFT,
      description: '把任务已出的成片落成剪映工程，落在用户素材库的默认分组里（同一任务重复调用只会得到同一个工程）。必须等任务渲染成功、有成片之后再调用；之后让用户在本机剪映里打开继续精修。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID' },
          title: { type: 'string', description: '工程名，省略就用任务标题' },
        },
        required: ['runId'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.START_JIANYING_EXPORT,
      description: '给已有剪映工程的任务排一条独立导出任务，把草稿收成成片文件。默认只排队：macOS 上剪映必须由人点导出，任务会停在「等人导出」。人点完导出后，把成片路径传 claimPath，按草稿的时长/画布做体检再入库；只有用户明确让你驱动这台 Mac 的剪映时才传 allowGui=true。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID，必须已经落过剪映工程' },
          allowGui: { type: 'boolean', description: '是否允许驱动本机剪映导出面板' },
          claimPath: { type: 'string', description: '用户人工导出的成片绝对路径；给了就只认领，不动剪映' },
        },
        required: ['runId'],
        additionalProperties: false,
      },
    },
  },
  {
    type: 'function',
    function: {
      name: AssistantToolName.OPEN_JIANYING_DRAFT,
      description: '把已有剪映草稿在这台 Mac 的剪映里打开，让人接着改；不导出、不保存。剪映会抢到前台，只在用户明确让你打开时用。',
      parameters: {
        type: 'object',
        properties: {
          runId: { type: 'string', description: '任务 ID，必须已经落过剪映工程' },
          draftPath: { type: 'string', description: '草稿目录绝对路径；省略就用任务记录里的草稿' },
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
