import { Module } from '@nestjs/common'
import { ContentModule } from '../content/content.module'
import { ProductionAssistantController } from './production-assistant.controller'
import { ProductionAssistantService } from './production-assistant.service'
import { ProductionController, ProductionWorkerController } from './production.controller'
import { ProductionScheduler } from './production.scheduler'
import { ProductionService } from './production.service'
import { ResearchFetcher } from './research-fetch'
import { StudioAssistantClient } from './studio-assistant.client'

@Module({
  imports: [ContentModule],
  controllers: [ProductionController, ProductionWorkerController, ProductionAssistantController],
  providers: [ProductionService, ProductionScheduler, ProductionAssistantService, StudioAssistantClient, ResearchFetcher],
  exports: [ProductionService, ProductionAssistantService],
})
export class ProductionModule {}
