import { expect, test } from '@playwright/test'

/** The selfie camera, with Chromium's fake camera: a moving pattern, so no face to find. */

test.use({ permissions: ['camera'], launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] } })
test.skip(({ browserName }) => browserName !== 'chromium', 'a fake camera exists only in Chromium')

test('the camera takes a picture, and the form says it holds no face', async ({ page }) => {
  await page.goto('/gallery')
  await page.getByRole('button', { name: 'Take a selfie' }).click()
  const shutter = page.getByRole('button', { name: 'Take the photo' })
  await expect(shutter).toBeEnabled({ timeout: 15_000 })
  await shutter.click()
  await expect(shutter).toBeHidden()
  await expect(page.getByRole('button', { name: 'Take another' })).toBeVisible()
  await page.getByLabel('Your name').fill('Camera Test')
  await page.getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('We could not find a face in that photo.')).toBeVisible({ timeout: 30_000 })
})
