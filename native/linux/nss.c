/* 仅为包内 ELF 选择 glibc 内置解析器，不修改宿主 NSS 配置。 */
#include <nss.h>
#include <stdlib.h>
#include <unistd.h>

__attribute__((constructor)) static void anp_builtin_nss(void) {
    /* Slim Node 的临时预载只用于本次 exec，不能传给宿主 shell 等子进程。 */
    unsetenv("LD_PRELOAD");
    const char *files[] = {"passwd", "group", "initgroups", "services", "protocols", NULL};
    if (__nss_configure_lookup("hosts", "files dns") != 0)
        _exit(126);
    for (int i = 0; files[i] != NULL; ++i)
        if (__nss_configure_lookup(files[i], "files") != 0)
            _exit(126);
}
