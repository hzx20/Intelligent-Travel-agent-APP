import {expect} from '@playwright/test'
export async function workspace(page,label){
  const button=page.getByRole('navigation',{name:'旅行工作区',exact:true}).getByRole('button',{name:new RegExp('^'+label)})
  await button.click();await expect(button).toHaveAttribute('aria-pressed','true')
}
