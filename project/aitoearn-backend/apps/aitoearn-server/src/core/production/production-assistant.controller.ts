import { Body, Controller, Get, Param, Post } from '@nestjs/common'
import { ApiTags } from '@nestjs/swagger'
import { GetToken, TokenInfo } from '@yikart/aitoearn-auth'
import { ApiDoc, createZodDto } from '@yikart/common'
import { z } from 'zod'
import { ProductionAssistantService } from './production-assistant.service'

const CreateAssistantSessionSchema = z.object({
  title: z.string().max(60).optional().describe('会话标题，不填用默认标题'),
})
class CreateAssistantSessionDto extends createZodDto(CreateAssistantSessionSchema, 'CreateAssistantSessionDto') {}

const SendAssistantMessageSchema = z.object({
  message: z.string().min(1).max(2000).describe('用户这一轮说的话'),
})
class SendAssistantMessageDto extends createZodDto(SendAssistantMessageSchema, 'SendAssistantMessageDto') {}

/**
 * 「AI 指挥台」对话接口。和 ProductionController 一样按用户凭证鉴权，
 * userId 只从 token 取，绝不接受请求体里的 userId。
 */
@ApiTags('Production')
@Controller('production/assistant')
export class ProductionAssistantController {
  constructor(private readonly assistant: ProductionAssistantService) {}

  @ApiDoc({ summary: '创建指挥台会话', body: CreateAssistantSessionDto.schema })
  @Post('sessions')
  async createSession(@GetToken() token: TokenInfo, @Body() body: CreateAssistantSessionDto) {
    return await this.assistant.createSession(token.id, body.title)
  }

  @ApiDoc({ summary: '会话详情（含全部消息）' })
  @Get('sessions/:id')
  async getSession(@GetToken() token: TokenInfo, @Param('id') id: string) {
    return await this.assistant.getSession(token.id, id)
  }

  @ApiDoc({ summary: '发一条消息并等待指挥台回复', body: SendAssistantMessageDto.schema })
  @Post('sessions/:id/messages')
  async sendMessage(
    @GetToken() token: TokenInfo,
    @Param('id') id: string,
    @Body() body: SendAssistantMessageDto,
  ) {
    return await this.assistant.sendMessage(token.id, id, body.message)
  }
}
