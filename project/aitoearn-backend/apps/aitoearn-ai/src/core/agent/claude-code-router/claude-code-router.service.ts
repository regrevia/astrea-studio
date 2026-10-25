import type { FSWatcher } from 'node:fs'
import { connect } from 'node:net'
import { ChildProcess, spawn } from 'node:child_process'
import { existsSync, mkdirSync, unlinkSync, watch, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common'
import { config } from '../../../config'
import { CLAUDE_CODE_ROUTER_PROVIDER_NAME } from '../agent.constants'

interface TransformerConfig {
  use: Array<string | [string, Record<string, unknown>] | Record<string, TransformerConfig>>
}

interface ProviderConfig {
  name: string
  api_base_url: string
  api_key: string
  models: string[]
  transformer?: TransformerConfig
}

interface RouterConfig {
  default: string
  background?: string
  think?: string
  longContext?: string
  longContextThreshold?: number
  webSearch?: string
  image?: string
}

interface ClaudeCodeRouterConfig {
  PORT?: number
  APIKEY?: string
  PROXY_URL?: string
  LOG?: boolean
  LOG_LEVEL?: 'fatal' | 'error' | 'warn' | 'info' | 'debug' | 'trace'
  HOST?: string
  NON_INTERACTIVE_MODE?: boolean
  Providers?: ProviderConfig[]
  Router?: RouterConfig
  API_TIMEOUT_MS?: number
}

@Injectable()
export class ClaudeCodeRouterService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ClaudeCodeRouterService.name)
  private childProcess: ChildProcess | null = null
  private fileWatcher: FSWatcher | null = null
  private shouldRestart = true
  private readonly sessionDir = join(process.cwd(), '.claude-session')
  private readonly configDir = join(this.sessionDir, '.claude-code-router')
  private readonly configPath = join(this.configDir, 'config.json')
  private readonly pidFilePath = join(this.sessionDir, '.claude-code-router.pid')
  private readonly routerPort = 3456
  private restartTimer: NodeJS.Timeout | null = null
  private rapidExits = 0

  async onModuleInit() {
    const routerConfig = config.agent

    const cliPath = require.resolve('@musistudio/claude-code-router/dist/cli.js')
    this.logger.debug(`找到 Claude Code Router CLI: ${cliPath}`)

    this.startFileWatcher()
    this.generateConfigFile(routerConfig)

    // 上一次 pm2 reload 留下的路由器还在监听，就复用它：再起一个只会因为端口
    // 被占而立刻退出，然后被无间隔重启，日志刷屏、CPU 打满。
    if (await this.isRouterServing()) {
      this.logger.warn(`Claude Code Router 已在 ${this.routerPort} 端口运行，直接复用`)
      return
    }
    this.startChildProcess(cliPath)
  }

  /** 端口已经有人应答就认为路由器活着，不必再起一个。 */
  private isRouterServing(): Promise<boolean> {
    return new Promise((resolve) => {
      const socket = connect({ host: '127.0.0.1', port: this.routerPort })
      const done = (alive: boolean) => {
        socket.removeAllListeners()
        socket.destroy()
        resolve(alive)
      }
      socket.setTimeout(800, () => done(false))
      socket.once('connect', () => done(true))
      socket.once('error', () => done(false))
    })
  }

  private scheduleRestart(cliPath: string, delayMs: number) {
    if (this.restartTimer) {
      clearTimeout(this.restartTimer)
    }
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null
      this.startChildProcess(cliPath)
    }, delayMs)
    this.restartTimer.unref()
  }

  async onModuleDestroy() {
    this.shouldRestart = false

    if (this.restartTimer) {
      clearTimeout(this.restartTimer)
      this.restartTimer = null
    }

    if (this.fileWatcher) {
      this.fileWatcher.close()
      this.fileWatcher = null
      this.logger.debug('文件监听器已停止')
    }

    if (this.childProcess) {
      this.logger.debug('正在停止 Claude Code Router...')
      this.childProcess.kill('SIGTERM')
      this.childProcess = null
    }
  }

  private generateConfigFile(routerConfig: typeof config.agent) {
    if (!existsSync(this.configDir)) {
      mkdirSync(this.configDir, { recursive: true })
      this.logger.debug(`创建配置目录: ${this.configDir}`)
    }

    const routerConfigFile = this.buildConfigFile(routerConfig)

    writeFileSync(this.configPath, JSON.stringify(routerConfigFile, null, 2), 'utf-8')
    this.logger.debug(`配置文件已生成: ${this.configPath}`)
  }

  private buildConfigFile(routerConfig: typeof config.agent): ClaudeCodeRouterConfig {
    return {
      PORT: 3456,
      APIKEY: 'ccr',
      NON_INTERACTIVE_MODE: true,
      Providers: [
        {
          name: CLAUDE_CODE_ROUTER_PROVIDER_NAME,
          api_base_url: routerConfig.baseUrl,
          api_key: routerConfig.apiKey,
          models: routerConfig.models,
          transformer: {
            use: [
              'Anthropic',
            ],
          },
        },
      ],
      Router: {
        default: `${CLAUDE_CODE_ROUTER_PROVIDER_NAME},${routerConfig.defaultModel}`,
        background: `${CLAUDE_CODE_ROUTER_PROVIDER_NAME},${routerConfig.backgroundModel}`,
        think: `${CLAUDE_CODE_ROUTER_PROVIDER_NAME},${routerConfig.thinkModel}`,
      },
    }
  }

  private startChildProcess(cliPath: string) {
    this.logger.debug('正在启动 Claude Code Router 子进程...')
    const startedAt = Date.now()

    this.childProcess = spawn('node', [cliPath, 'start'], {
      cwd: this.sessionDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        HOME: this.sessionDir,
      },
    })

    this.childProcess.stdout?.on('data', (data) => {
      this.logger.debug(`Claude Code Router stdout: ${data.toString().trim()}`)
    })

    this.childProcess.stderr?.on('data', (data) => {
      this.logger.warn(`Claude Code Router stderr: ${data.toString().trim()}`)
    })

    this.childProcess.on('exit', (code, signal) => {
      if (code !== null) {
        this.logger.debug(`Claude Code Router 进程退出，退出码: ${code}`)
      }
      else if (signal) {
        this.logger.debug(`Claude Code Router 进程被信号终止: ${signal}`)
      }
      this.childProcess = null

      if (!this.shouldRestart) {
        return
      }
      if (Date.now() - startedAt < 5000) {
        this.rapidExits += 1
        const delay = Math.min(60_000, 1000 * 2 ** Math.min(this.rapidExits, 6))
        this.logger.warn(
          `Claude Code Router 启动后 ${Date.now() - startedAt}ms 就退出，`
          + `${delay}ms 后重试（连续第 ${this.rapidExits} 次）`,
        )
        this.scheduleRestart(cliPath, delay)
        return
      }
      this.rapidExits = 0
      this.logger.debug('正在重启 Claude Code Router...')
      this.scheduleRestart(cliPath, 500)
    })

    this.childProcess.on('error', (error) => {
      this.logger.error({ error }, 'Claude Code Router 进程错误')
      this.childProcess = null
    })

    this.logger.debug('Claude Code Router 子进程已启动')
  }

  private removePidFile() {
    if (existsSync(this.pidFilePath)) {
      try {
        unlinkSync(this.pidFilePath)
        this.logger.debug(`已删除 pid 文件: ${this.pidFilePath}`)
      }
      catch (error) {
        this.logger.warn({ error }, '删除 pid 文件失败')
      }
    }
  }

  private startFileWatcher() {
    if (!existsSync(this.configDir)) {
      mkdirSync(this.configDir, { recursive: true })
      this.logger.debug(`创建配置目录: ${this.configDir}`)
    }

    if (this.fileWatcher) {
      this.fileWatcher.close()
    }

    this.removePidFile()

    this.logger.debug(`开始监听目录: ${this.sessionDir}`)

    this.fileWatcher = watch(this.sessionDir, (eventType, filename) => {
      if (filename === '.claude-code-router.pid') {
        this.logger.debug(`检测到 pid 文件事件: ${eventType}`)
        this.removePidFile()
      }
    })

    this.fileWatcher.on('error', (error) => {
      this.logger.error({ error }, '文件监听器错误')
    })
  }
}
