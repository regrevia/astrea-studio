/**
 * AvatarPanel - 虚拟形象的素材管理
 *
 * 动作和表情是全套服装共用的一套，服装只提供身体：装一套新衣服不用重画表情，
 * 加一个表情也不用动任何一套衣服。这里只做三件事：看现状、换默认服装、装新服装。
 */

'use client'

import type { AvatarModel } from '@/api/production/production.types'
import { getCharacterModel, importCharacterOutfit, setCharacterOutfit } from '@/api/production/production.api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Loader2, Save, Upload, UserRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

const CHARACTER_ID = 'whale'

export default function AvatarPanel() {
  const [model, setModel] = useState<AvatarModel | null>(null)
  const [outfit, setOutfit] = useState('')
  const [saving, setSaving] = useState(false)
  const [src, setSrc] = useState('')
  const [name, setName] = useState('')
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const data = await getCharacterModel(CHARACTER_ID)
    setModel(data)
    setOutfit(data?.default_outfit ?? '')
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  async function handleSave() {
    if (!outfit || outfit === model?.default_outfit) {
      return
    }
    setSaving(true)
    setError('')
    const res = await setCharacterOutfit(outfit, CHARACTER_ID)
    setSaving(false)
    if (!res.ok) {
      setError(res.message ?? '')
      return
    }
    await reload()
  }

  async function handleImport(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!src.trim() || !name.trim()) {
      setError('填素材目录和服装名')
      return
    }
    setImporting(true)
    setError('')
    const res = await importCharacterOutfit({ src: src.trim(), id: name.trim() }, CHARACTER_ID)
    setImporting(false)
    if (!res.ok) {
      setError(res.message ?? '')
      return
    }
    setSrc('')
    setName('')
    await reload()
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <UserRound className="size-4" />
          虚拟形象
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {model
          ? (
              <>
                <div className="flex flex-wrap items-end gap-3">
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs">默认服装</Label>
                    <div className="flex items-center gap-2">
                      <Select value={outfit} onValueChange={setOutfit}>
                        <SelectTrigger className="w-56">
                          <SelectValue placeholder="选一套" />
                        </SelectTrigger>
                        <SelectContent>
                          {model.outfits.map(o => (
                            <SelectItem key={o.id} value={o.id}>{o.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button
                        type="button"
                        size="sm"
                        className="cursor-pointer"
                        disabled={saving || !outfit || outfit === model.default_outfit}
                        onClick={handleSave}
                      >
                        {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                        保存
                      </Button>
                    </div>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {model.poses.length}
                    {' '}
                    个动作、
                    {model.expressions.length}
                    {' '}
                    个表情，所有服装共用
                  </p>
                </div>

                <div className="flex flex-col gap-1">
                  {model.outfits.map(o => (
                    <div key={o.id} className="flex items-center justify-between text-sm">
                      <span>{o.label}</span>
                      <span className="text-[11px] text-muted-foreground">
                        {o.poses.length}
                        {' '}
                        个动作 ·
                        {' '}
                        {o.has_expression_layer ? '动作与表情分开' : '动作自带表情'}
                      </span>
                    </div>
                  ))}
                </div>

                <form className="flex flex-wrap items-end gap-2" onSubmit={handleImport}>
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs">素材目录</Label>
                    <Input
                      className="w-72"
                      value={src}
                      placeholder="/Users/…/06_spring"
                      onChange={e => setSrc(e.target.value)}
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs">服装名</Label>
                    <Input
                      className="w-40"
                      value={name}
                      placeholder="06_spring"
                      onChange={e => setName(e.target.value)}
                    />
                  </div>
                  <Button type="submit" size="sm" className="cursor-pointer" disabled={importing}>
                    {importing ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
                    导入
                  </Button>
                </form>
              </>
            )
          : <p className="text-[11px] text-muted-foreground">形象服务没响应</p>}
        {error ? <p className="text-[11px] text-destructive">{error}</p> : null}
      </CardContent>
    </Card>
  )
}
