import { Injectable, Logger } from '@nestjs/common'
import { Cron, CronExpression } from '@nestjs/schedule'
import { ProductionService } from './production.service'

@Injectable()
export class ProductionScheduler {
  private readonly logger = new Logger(ProductionScheduler.name)

  constructor(private readonly production: ProductionService) {}

  /**
   * worker 被 kill -9 后不会再续约，必须由服务端定时把过期租约捞出来回收，
   * 否则 job 永远 running、run 永远进行中，前端一直挂着僵尸任务。
   */
  @Cron(CronExpression.EVERY_MINUTE)
  async reapExpiredLeases() {
    try {
      const requeued = await this.production.reapExpiredLeases()
      if (requeued > 0) {
        this.logger.log(`requeued ${requeued} run(s) whose worker lease expired`)
      }
    }
    catch (error) {
      // 调度器里抛出的异常会打断同进程其它 cron，这里只记录、交给下一分钟重试
      this.logger.error(error, 'Failed to reap expired production leases')
    }
  }
}
