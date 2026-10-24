import { Module } from '@nestjs/common'
import { ApiTags } from '@nestjs/swagger'
import { McpModule } from '@yikart/nest-mcp'
import { ProductionMcpController } from './production.mcp.controller'
import { ProductionModule } from './production.module'

/**
 * Production tools need their own MCP server, not just a controller inside
 * ProductionModule.
 *
 * McpRegistryService only scans subtrees of modules that import McpModule, so a
 * controller registered anywhere else is silently ignored — no error, no tool,
 * which is exactly how the production tools went missing the first time.
 */
@Module({
  imports: [
    ProductionModule,
    McpModule.forRoot({
      name: 'production',
      version: '1.0.0',
      apiPrefix: 'production',
      decorators: [ApiTags('MCP/Production')],
    }),
  ],
  providers: [ProductionMcpController],
})
export class ProductionMcpModule {}
