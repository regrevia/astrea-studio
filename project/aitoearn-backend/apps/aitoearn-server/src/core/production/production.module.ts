import { Module } from '@nestjs/common'
import { ContentModule } from '../content/content.module'
import { ProductionController, ProductionWorkerController } from './production.controller'
import { ProductionScheduler } from './production.scheduler'
import { ProductionService } from './production.service'

@Module({
  imports: [ContentModule],
  controllers: [ProductionController, ProductionWorkerController],
  providers: [ProductionService, ProductionScheduler],
  exports: [ProductionService],
})
export class ProductionModule {}
