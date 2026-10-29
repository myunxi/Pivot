'use client';

import { getDesktopConfig } from '@/lib/runtime-config';

/**
 * Attachment transport for this build.
 *
 * Plain web goes through the cookie-authenticated Next proxy
 * (`/api/attachments`). Browser direct mode has no cloud session, so it talks
 * straight to the local daemon: uploads carry the Bearer token, and `<img>`
 * URLs carry `?token=` because an image tag cannot set an Authorization header.
 */
function directConfig() {
  const config = getDesktopConfig();
  return config?.direct === true ? config : null;
}

function baseUrl(config: { apiBase: string }): string {
  return config.apiBase.replace(/\/+$/, '');
}

export function getAttachmentUploadUrl(): string {
  const config = directConfig();
  return config ? `${baseUrl(config)}/api/v1/attachments` : '/api/attachments';
}

export function getAttachmentUploadHeaders(): Record<string, string> {
  const config = directConfig();
  return config?.token ? { Authorization: `Bearer ${config.token}` } : {};
}

export function getAttachmentUrl(attachmentId: string): string {
  const id = encodeURIComponent(attachmentId);
  const config = directConfig();
  if (!config) return `/api/attachments/${id}`;
  const token = encodeURIComponent(config.token ?? '');
  return `${baseUrl(config)}/api/v1/attachments/${id}?token=${token}`;
}
