include $(TOPDIR)/rules.mk

PKG_NAME:=nginx-stream-manager
PKG_VERSION:=0.3.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT

include $(INCLUDE_DIR)/package.mk

define Package/nginx-stream-manager
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Generic Nginx TCP/UDP stream manager
  PKGARCH:=all
  DEPENDS:=+nginx +nginx-mod-stream +python3 +ip-full +nftables-json +firewall4 +kmod-nft-socket +rpcd
endef

define Package/nginx-stream-manager/description
 UCI-based stream configuration, transparent source-IP preservation,
 policy routing, bounded logs, and a narrowly scoped rpcd interface.
endef

define Package/nginx-stream-manager/conffiles
/etc/config/nginx_stream_manager
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Compile
endef

define Package/nginx-stream-manager/install
	$(INSTALL_DIR) $(1)/usr/libexec/rpcd $(1)/etc/config $(1)/etc/init.d
	$(INSTALL_DIR) $(1)/etc/hotplug.d/iface $(1)/usr/share/nftables.d/chain-pre/input
	$(INSTALL_DATA) ./files/nsm_importer.py $(1)/usr/libexec/nsm_importer.py
	$(INSTALL_BIN) ./files/nsm.py $(1)/usr/libexec/nginx-stream-manager
	$(INSTALL_BIN) ./files/maintenance $(1)/usr/libexec/nginx-stream-manager-maintenance
	$(INSTALL_BIN) ./files/rpc $(1)/usr/libexec/rpcd/luci.nginx-stream-manager
	$(INSTALL_BIN) ./files/nginx-stream-manager.init $(1)/etc/init.d/nginx-stream-manager
	$(INSTALL_CONF) ./files/nginx_stream_manager $(1)/etc/config/nginx_stream_manager
	$(INSTALL_DATA) ./files/hotplug $(1)/etc/hotplug.d/iface/95-nginx-stream-manager
	$(INSTALL_DATA) ./files/return.nft $(1)/usr/share/nftables.d/chain-pre/input/90-nginx-stream-manager.nft
endef

define Package/nginx-stream-manager/postinst
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
/etc/init.d/rpcd restart || exit 1
fw4 check && /etc/init.d/firewall reload
endef

define Package/nginx-stream-manager/prerm
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
/etc/init.d/nginx-stream-manager stop || exit 1
/usr/libexec/nginx-stream-manager cleanup || exit 1
[ "$$PKG_UPGRADE" = 1 ] || /etc/init.d/nginx-stream-manager disable
exit 0
endef

define Package/nginx-stream-manager/postrm
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] && exit 0
[ "$$PKG_UPGRADE" = 1 ] && exit 0
/etc/init.d/rpcd restart || exit 1
fw4 check && /etc/init.d/firewall reload
endef

$(eval $(call BuildPackage,nginx-stream-manager))
