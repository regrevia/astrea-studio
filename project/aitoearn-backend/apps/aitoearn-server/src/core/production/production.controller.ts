import { randomUUID } from 'node:crypto'
import {
  Body,
  Controller,
  Get,
  Headers,
  HttpCode,
  Param,
  Post,
  Query,
  UnauthorizedException,
} from '@nestjs/common'
import { ApiTags } from '@nestjs/swagger'
import { GetToken, Public, TokenInfo } from '@yikart/aitoearn-auth'
import { ApiDoc, AppException, createZodDto, ResponseCode } from '@yikart/common'
import { ProductionJobStatus, PublicationDispatcher, PublicationStatus } from '@yikart/mongodb'
import { z } from 'zod'
import { ProductionService } from './production.service'
import { normalizeRunSpec } from './production.run-spec'

const CreateProjectSchema = z.object({
  name: z.string().min(1).describe('项目名'),
  sourceRepo: z.string().optional().describe('上游仓库'),
  materialGroupId: z.string().optional().describe('草稿箱 ID'),
  brand: z.record(z.string(), z.unknown()).optional().describe('品牌信息'),
  schedulePolicy: z.record(z.string(), z.unknown()).optional().describe('排期策略'),
})
class CreateProjectDto extends createZodDto(CreateProjectSchema, 'CreateProductionProjectDto') {}

const UpdateProjectSchema = z.object({
  name: z.string().min(1).optional(),
  sourceRepo: z.string().optional(),
  materialGroupId: z.string().optional(),
  brand: z.record(z.string(), z.unknown()).optional(),
  schedulePolicy: z.record(z.string(), z.unknown()).optional(),
  accountBindings: z.array(z.record(z.string(), z.unknown())).optional(),
})
class UpdateProjectDto extends createZodDto(UpdateProjectSchema, 'UpdateProductionProjectDto') {}

const CreateRunSchema = z.object({
  projectId: z.string().describe('项目 ID'),
  title: z.string().min(1).describe('标题'),
  brief: z.string().default('').describe('用户原始需求'),
  engine: z.string().optional().describe('渲染通道：motion/slides/jianying/timeline，缺省走 MoneyPrinterTurbo'),
  template: z.string().optional().describe('版式模板，比如 factcard'),
  spec: z.record(z.string(), z.unknown()).optional().describe('结构化制作规格'),
  facts: z.record(z.string(), z.unknown()).optional().describe('项目事实'),
})
class CreateRunDto extends createZodDto(CreateRunSchema, 'CreateProductionRunDto') {}

const BatchRunSchema = CreateRunSchema.extend({
  count: z.number().int().min(1).max(8).default(2).describe('排几条：同一份稿子出多个母版'),
})
class BatchRunDto extends createZodDto(BatchRunSchema, 'BatchProductionRunDto') {}

const ReviseRunSchema = z.object({
  brief: z.string().optional().describe('修改后的需求'),
  spec: z.record(z.string(), z.unknown()).optional().describe('规格补丁'),
})
class ReviseRunDto extends createZodDto(ReviseRunSchema, 'ReviseProductionRunDto') {}

const JianyingExportSchema = z.object({
  allowGui: z.boolean().optional().describe('是否允许驱动剪映导出面板（用户正在用机器时会退避）'),
  draftPath: z.string().optional().describe('草稿目录；省略就用任务里记下的剪映工程'),
  waitIdle: z.number().min(0).max(600).optional()
    .describe('等用户空闲多少秒再动鼠标；0 表示立刻就导（仍然点完即还台）'),
})
class JianyingExportDto extends createZodDto(JianyingExportSchema, 'ExportProductionDraftDto') {}

const AttachMaterialSchema = z.object({
  groupId: z.string().describe('草稿箱 ID'),
  title: z.string().describe('草稿标题'),
  desc: z.string().optional().describe('正文'),
  topics: z.array(z.string()).optional().describe('话题'),
  videoUrl: z.string().describe('可访问的视频地址'),
  coverUrl: z.string().optional().describe('封面地址'),
  videoPath: z.string().optional().describe('本机绝对路径（仅记录）'),
  duration: z.number().optional().describe('时长秒'),
})
class AttachMaterialDto extends createZodDto(AttachMaterialSchema, 'AttachProductionMaterialDto') {}

const WorkerMaterialSchema = z.object({
  title: z.string().optional(),
  desc: z.string().optional(),
  topics: z.array(z.string()).optional(),
  videoUrl: z.string().describe('对象存储 key（worker 上传后的返回）'),
  duration: z.number().optional(),
  groupId: z.string().optional(),
})
class WorkerMaterialDto extends createZodDto(WorkerMaterialSchema, 'WorkerAttachMaterialDto') {}

const PlanPublicationSchema = z.object({
  runId: z.string(),
  platform: z.string().describe('平台，如 douyin'),
  dispatcher: z.enum(PublicationDispatcher).describe('执行方'),
  accountRef: z.string().describe('真实账号标识'),
  accountName: z.string().optional(),
  plannedAt: z.string().optional().describe('计划时间 ISO'),
  content: z.record(z.string(), z.unknown()).optional().describe('发布文案包'),
})
class PlanPublicationDto extends createZodDto(PlanPublicationSchema, 'PlanPublicationDto') {}

const RecordSubmissionSchema = z.object({
  status: z.enum(PublicationStatus).describe('目标状态'),
  remotePostId: z.string().optional().describe('平台作品 ID'),
  remoteUrl: z.string().optional().describe('作品链接'),
  evidence: z.record(z.string(), z.unknown()).optional().describe('回读证据'),
  error: z.string().optional(),
})
class RecordSubmissionDto extends createZodDto(RecordSubmissionSchema, 'RecordSubmissionDto') {}

const RegisterWorkerSchema = z.object({
  name: z.string().min(1).describe('worker 名称'),
  capabilities: z.array(z.string()).default([]).describe('支持的作业类型'),
  version: z.string().default(''),
})
class RegisterWorkerDto extends createZodDto(RegisterWorkerSchema, 'RegisterWorkerDto') {}

const ClaimSchema = z.object({
  lane: z.string().default('render').describe('作业队列：render/publish'),
})
class ClaimDto extends createZodDto(ClaimSchema, 'WorkerClaimDto') {}

const CapabilitiesSchema = z.object({
  capabilities: z.array(z.string().min(1)).describe('本进程实际支持的作业类型'),
})
class CapabilitiesDto extends createZodDto(CapabilitiesSchema, 'WorkerCapabilitiesDto') {}

const HeartbeatSchema = z.object({
  leaseVersion: z.number().int().describe('租约版本'),
  progress: z.number().min(0).max(100).optional(),
  stage: z.string().optional(),
  message: z.string().optional(),
})
class HeartbeatDto extends createZodDto(HeartbeatSchema, 'WorkerHeartbeatDto') {}

const CompleteSchema = z.object({
  leaseVersion: z.number().int(),
  result: z.record(z.string(), z.unknown()).default({}),
  runPatch: z.record(z.string(), z.unknown()).optional(),
})
class CompleteDto extends createZodDto(CompleteSchema, 'WorkerCompleteDto') {}

const FailSchema = z.object({
  leaseVersion: z.number().int(),
  error: z.string().default('unknown error'),
  status: z.enum(ProductionJobStatus).optional(),
})
class FailDto extends createZodDto(FailSchema, 'WorkerFailDto') {}

const WorkerEventSchema = z.object({
  kind: z.string().describe('事件类型'),
  stage: z.string().optional(),
  progress: z.number().min(0).max(100).optional(),
  message: z.string().optional(),
  data: z.record(z.string(), z.unknown()).optional(),
})
class WorkerEventDto extends createZodDto(WorkerEventSchema, 'WorkerEventDto') {}

/**
 * Studio/panel API. Authenticated with a user credential (JWT or local x-api-key),
 * so every userId comes from the auth context and never from the body.
 */
@ApiTags('Production')
@Controller('production')
export class ProductionController {
  constructor(private readonly production: ProductionService) {}

  @ApiDoc({ summary: '项目列表' })
  @Get('projects')
  async listProjects(@GetToken() token: TokenInfo) {
    return await this.production.listProjects(token.id)
  }

  @ApiDoc({ summary: '创建项目', body: CreateProjectDto.schema })
  @Post('projects')
  async createProject(@GetToken() token: TokenInfo, @Body() body: CreateProjectDto) {
    return await this.production.createProject(token.id, body)
  }

  @ApiDoc({ summary: '更新项目', body: UpdateProjectDto.schema })
  @Post('projects/:id')
  async updateProject(
    @GetToken() token: TokenInfo,
    @Param('id') id: string,
    @Body() body: UpdateProjectDto,
  ) {
    return await this.production.updateProject(token.id, id, body)
  }

  @ApiDoc({ summary: '任务列表' })
  @Get('runs')
  async listRuns(
    @GetToken() token: TokenInfo,
    @Query('pageNo') pageNo = '1',
    @Query('pageSize') pageSize = '20',
    @Query('status') status?: string,
  ) {
    return await this.production.listRuns(token.id, Number(pageNo) || 1, Number(pageSize) || 20, status)
  }

  @ApiDoc({ summary: '新建任务（立即返回 runId）', body: CreateRunDto.schema })
  @Post('runs')
  async createRun(@GetToken() token: TokenInfo, @Body() body: CreateRunDto) {
    const run = await this.production.createRun(token.id, {
      ...body,
      spec: normalizeRunSpec(body),
    })
    return {
      runId: run.id,
      status: run.status,
      revision: run.revision,
      statusUrl: `/api/production/runs/${run.id}`,
    }
  }

  @ApiDoc({ summary: '批量出片：同一份稿子排 count 条，返回全部 runId', body: BatchRunDto.schema })
  @Post('runs/batch')
  async createRunBatch(@GetToken() token: TokenInfo, @Body() body: BatchRunDto) {
    const { count, ...one } = body
    // 串行入队：worker 本来就按队列一条条渲，并发提交只会白等
    const runs = await this.production.createRunBatch(token.id, {
      ...one,
      spec: normalizeRunSpec(one),
    }, count)
    return {
      runIds: runs.map(run => run.id),
      count: runs.length,
      statusUrl: `/api/production/runs/${runs[0]?.id ?? ''}`,
    }
  }

  @ApiDoc({ summary: '任务详情' })
  @Get('runs/:id')
  async getRun(@GetToken() token: TokenInfo, @Param('id') id: string) {
    return await this.production.runDetail(token.id, id)
  }

  @ApiDoc({ summary: '任务事件流' })
  @Get('runs/:id/events')
  async getEvents(
    @GetToken() token: TokenInfo,
    @Param('id') id: string,
    @Query('afterSeq') afterSeq = '0',
  ) {
    return await this.production.listEvents(token.id, id, Number(afterSeq) || 0)
  }

  @ApiDoc({ summary: '修订任务（生成新 revision）', body: ReviseRunDto.schema })
  @Post('runs/:id/revise')
  async reviseRun(@GetToken() token: TokenInfo, @Param('id') id: string, @Body() body: ReviseRunDto) {
    const run = await this.production.reviseRun(token.id, id, body)
    return { runId: id, revision: run?.revision ?? null, status: run?.status ?? null }
  }

  @ApiDoc({ summary: '重试任务' })
  @Post('runs/:id/retry')
  async retryRun(@GetToken() token: TokenInfo, @Param('id') id: string) {
    return await this.production.retryRun(token.id, id)
  }

  @ApiDoc({ summary: '取消任务' })
  @Post('runs/:id/cancel')
  async cancelRun(@GetToken() token: TokenInfo, @Param('id') id: string) {
    return await this.production.cancelRun(token.id, id)
  }

  @ApiDoc({ summary: '从剪映工程收导出产物（独立任务，默认只排队等人点导出）', body: JianyingExportDto.schema })
  @Post('runs/:id/export')
  async startJianyingExport(
    @GetToken() token: TokenInfo,
    @Param('id') id: string,
    @Body() body: JianyingExportDto,
  ) {
    return await this.production.startJianyingExport(token.id, id, body)
  }

  @ApiDoc({ summary: '把成片写入草稿箱', body: AttachMaterialDto.schema })
  @Post('runs/:id/material')
  async attachMaterial(
    @GetToken() token: TokenInfo,
    @Param('id') id: string,
    @Body() body: AttachMaterialDto,
  ) {
    return await this.production.attachMaterial(token.id, id, body)
  }

  @ApiDoc({ summary: '登记发布意图', body: PlanPublicationDto.schema })
  @Post('publications')
  async planPublication(@GetToken() token: TokenInfo, @Body() body: PlanPublicationDto) {
    const { attempt, deduped } = await this.production.planPublication(token.id, body)
    return { attemptId: attempt.id, status: attempt.status, deduped }
  }

  @ApiDoc({ summary: '发布尝试列表' })
  @Get('publications')
  async listPublications(@GetToken() token: TokenInfo, @Query('runId') runId?: string) {
    return await this.production.listAttempts(token.id, runId)
  }

  @ApiDoc({ summary: '开始提交（先落库再操作浏览器）' })
  @Post('publications/:id/submit')
  async beginSubmit(@GetToken() token: TokenInfo, @Param('id') id: string) {
    const attempt = await this.production.beginSubmission(token.id, id)
    return { attemptId: id, status: attempt?.status }
  }

  @ApiDoc({ summary: '回写提交结果', body: RecordSubmissionDto.schema })
  @Post('publications/:id/reconcile')
  async reconcile(
    @GetToken() token: TokenInfo,
    @Param('id') id: string,
    @Body() body: RecordSubmissionDto,
  ) {
    return await this.production.recordSubmission(token.id, id, body)
  }

  @ApiDoc({ summary: 'Worker 与运行时健康' })
  @Get('health')
  async health(@GetToken() token: TokenInfo) {
    return {
      workers: await this.production.workerHealth(token.id),
      serverTime: new Date().toISOString(),
    }
  }

  @ApiDoc({ summary: '注册 worker（返回一次性 token）', body: RegisterWorkerDto.schema })
  @Post('workers/register')
  async registerWorker(@GetToken() token: TokenInfo, @Body() body: RegisterWorkerDto) {
    return await this.production.registerWorker(token.id, body.name, body.capabilities, body.version)
  }
}

/**
 * Worker-facing endpoints. Authenticated by a scoped worker token (`x-worker-token`)
 * instead of a user credential; the bound userId always comes from the token row.
 */
@ApiTags('Production/Worker')
@Controller('production/worker')
export class ProductionWorkerController {
  constructor(private readonly production: ProductionService) {}

  private async auth(token?: string) {
    if (!token) {
      throw new UnauthorizedException()
    }
    const worker = await this.production.resolveWorker(token)
    if (!worker) {
      throw new AppException(ResponseCode.ProductionWorkerUnauthorized)
    }
    return worker
  }

  @Public()
  @HttpCode(200)
  @Post('claim')
  async claim(@Headers('x-worker-token') token: string | undefined, @Body() body: ClaimDto) {
    const worker = await this.auth(token)
    const job = await this.production.claimJob(
      { id: worker.id, userId: worker.userId, capabilities: worker.capabilities },
      body.lane,
    )
    return { job, workerId: worker.id }
  }

  @Public()
  @HttpCode(200)
  @Post('jobs/:id/heartbeat')
  async heartbeat(
    @Headers('x-worker-token') token: string | undefined,
    @Param('id') id: string,
    @Body() body: HeartbeatDto,
  ) {
    const worker = await this.auth(token)
    return await this.production.heartbeatJob(
      worker.id,
      id,
      body.leaseVersion,
      body.progress,
      body.stage,
      body.message,
    )
  }

  @Public()
  @HttpCode(200)
  @Post('jobs/:id/events')
  async jobEvent(
    @Headers('x-worker-token') token: string | undefined,
    @Param('id') id: string,
    @Body() body: WorkerEventDto,
  ) {
    const worker = await this.auth(token)
    return await this.production.appendJobEvent(worker.id, id, {
      kind: body.kind,
      stage: body.stage,
      progress: body.progress,
      message: body.message,
      data: body.data,
    })
  }

  @Public()
  @HttpCode(200)
  @Post('jobs/:id/complete')
  async complete(
    @Headers('x-worker-token') token: string | undefined,
    @Param('id') id: string,
    @Body() body: CompleteDto,
  ) {
    const worker = await this.auth(token)
    return await this.production.completeJob(worker.id, id, body.leaseVersion, body.result, body.runPatch ?? {})
  }

  @Public()
  @HttpCode(200)
  @Post('jobs/:id/fail')
  async fail(
    @Headers('x-worker-token') token: string | undefined,
    @Param('id') id: string,
    @Body() body: FailDto,
  ) {
    const worker = await this.auth(token)
    return await this.production.failJob(
      worker.id,
      id,
      body.leaseVersion,
      body.error,
      body.status ?? ProductionJobStatus.FAILED,
    )
  }

  @Public()
  @HttpCode(200)
  @Post('runs/:id/material')
  async attachMaterial(
    @Headers('x-worker-token') token: string | undefined,
    @Param('id') id: string,
    @Body() body: WorkerMaterialDto,
  ) {
    const worker = await this.auth(token)
    return await this.production.attachMaterialForWorker(worker.userId, id, {
      title: body.title,
      desc: body.desc,
      topics: body.topics,
      videoKey: body.videoUrl,
      duration: body.duration,
      groupId: body.groupId,
    })
  }

  @Public()
  @HttpCode(200)
  @ApiDoc({ summary: 'worker 自刷新能力清单' })
  @Post('capabilities')
  async refreshCapabilities(
    @Headers('x-worker-token') token: string | undefined,
    @Body() body: CapabilitiesDto,
  ) {
    const worker = await this.auth(token)
    return await this.production.refreshWorkerCapabilities(worker.id, body.capabilities)
  }

  @Public()
  @HttpCode(200)
  @Post('workers/heartbeat')
  async workerHeartbeat(@Headers('x-worker-token') token: string | undefined) {
    const worker = await this.auth(token)
    return {
      ok: true,
      workerId: worker.id,
      serverTime: new Date().toISOString(),
      requestId: randomUUID(),
    }
  }
}
