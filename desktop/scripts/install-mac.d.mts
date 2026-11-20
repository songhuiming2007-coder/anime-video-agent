// 给 tests/static/installMac.test.ts 的类型声明（脚本本体为纯 .mjs）
export const DEFAULT_IDENTITY: string;
export const DEFAULT_INSTALL_PATH: string;
export const BUNDLE_ID: string;

export interface SigningIdentity {
  sha1: string;
  name: string;
}

export function parseIdentities(stdout: string): SigningIdentity[];
export function resolveIdentitySha1(identities: SigningIdentity[], query: string): string;
export function validateDesignatedRequirement(drOutput: string, bundleId?: string): string;
export function isMachOBuffer(buf: Buffer): boolean;
export function validateInstallPath(targetPath: string): string;
export function collectSignTargets(appPath: string): string[];
