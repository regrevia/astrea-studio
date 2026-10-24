import { Module } from '@nestjs/common'
import { ContentModule } from '../content/content.module'
import { ProductionController, ProductionWorkerController } from './production.controller'
import { ProductionService } from './production.service'

@Module({
  imports: [ContentModule],
  controllers: [ProductionController, ProductionWorkerController],
  providers: [ProductionService],
  exports: [ProductionService],
})
export class ProductionModule {}
