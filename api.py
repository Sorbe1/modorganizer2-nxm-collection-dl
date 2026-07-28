import json
import time
import traceback
import urllib.error
import urllib.request

from . import var
from .collection_helpers import quotaLimitMessage

qDebug = var.debug


def nxmFetch(requestData):
    operation_name = requestData.get("operationName", "<unknown>")
    variables = requestData.get("variables", {})
    jsonData = json.dumps(requestData).encode("utf-8")
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20211714 Firefox/135.0",
        "Accept": "application/json",
        "Content-type": "application/json",
    }
    qDebug(
        "[NXMColDL API] Request start: "
        f"operation={operation_name}, variables={var.cleanJson(variables)}, "
        f"bytes={len(jsonData)}"
    )
    request = urllib.request.Request(
        "https://api.nexusmods.com/v2/graphql", data=jsonData, headers=headers
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request) as response:
            content = response.read()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            qDebug(
                "[NXMColDL API] Response received: "
                f"operation={operation_name}, status={response.status}, "
                f"bytes={len(content)}, elapsed_ms={elapsed_ms}"
            )
            resp = json.loads(content)
            if "errors" in resp:
                qDebug(
                    "[NXMColDL API] GraphQL errors: "
                    f"operation={operation_name}, errors={var.cleanJson(resp['errors'])}"
                )
            data = resp.get("data")
            qDebug(
                "[NXMColDL API] Response data keys: "
                f"operation={operation_name}, keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
            )
            return data
    except urllib.error.HTTPError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        quota_message = quotaLimitMessage(e.code, e.headers, body)
        if quota_message:
            var.lastQuotaLimitMessage = quota_message
        qDebug(
            "[NXMColDL API] HTTP error: "
            f"operation={operation_name}, status={e.code}, reason={e.reason}, "
            f"elapsed_ms={elapsed_ms}, quota={quota_message!r}, body={body[:1000]}"
        )
        return None
    except urllib.error.URLError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        qDebug(
            "[NXMColDL API] URL error: "
            f"operation={operation_name}, reason={e.reason}, elapsed_ms={elapsed_ms}"
        )
        return None
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        qDebug(
            "[NXMColDL API] Unexpected error: "
            f"operation={operation_name}, error={e}, elapsed_ms={elapsed_ms}, "
            f"traceback={traceback.format_exc()}"
        )
        return None


def fetchRevisions(url):
    qDebug(f"[NXMColDL] Fetching revisions for {url}")
    query = """
            query CollectionRevisions($domainName: String, $slug: String!) {
                collection(domainName: $domainName, slug: $slug) {
                    revisions {
                        createdAt
                        revisionNumber
                    }
                }
            }
            """
    jsonData = {
        "query": query,
        "variables": {"domainName": var.game, "slug": var.collection},
        "operationName": "CollectionRevisions",
    }
    return nxmFetch(jsonData)


def fetchInfo(url):
    qDebug(f"[NXMColDL] Fetching collection info for {url}")
    query = """
            query CollectionManifestInfo($domainName: String, $slug: String!) {
                collection(domainName: $domainName, slug: $slug) {
                    name
                    summary
                    user {
                        name
                    }
                    tileImage {
                        thumbnailUrl(size: small)
                    }
                }
            }
            """
    jsonData = {
        "query": query,
        "variables": {"domainName": var.game, "slug": var.collection},
        "operationName": "CollectionManifestInfo",
    }
    return nxmFetch(jsonData)


def fetchModInfo(url):
    qDebug(f"[NXMColDL] Fetching mod info for {url}")
    query = """
            query CollectionRevisionMods($revision: Int, $slug: String!, $viewAdultContent: Boolean) {
                collectionRevision(revision: $revision, slug: $slug, viewAdultContent: $viewAdultContent) {
                    externalResources {
                        id
                        name
                        resourceType
                        resourceUrl
                    }
                    modFiles {
                        fileId
                        optional
                        file {
                            fileId
                            name
                            scanned
                            size
                            sizeInBytes
                            version
                            mod {
                                adult
                                author
                                category
                                modId
                                name
                                pictureUrl
                                summary
                                version
                                game {
                                    domainName
                                }
                                uploader {
                                    avatar
                                    memberId
                                    name
                                }
                            }
                        }
                    }
                }
            }
            """
    jsonData = {
        "query": query,
        "variables": {
            "revision": var.revision,
            "slug": var.collection,
            "viewAdultContent": True,
        },
        "operationName": "CollectionRevisionMods",
    }
    return nxmFetch(jsonData)
