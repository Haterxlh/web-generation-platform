package com.bill.web_generation_platform.TestController;

import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/redis-test")
public class RedisTestController {

    private final StringRedisTemplate redisTemplate;

    RedisTestController(StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @GetMapping("/write")
    public String write() {
        redisTemplate.opsForValue().set("web-key", "Hello from Browser!");
        return "写入成功！";
    }

    @GetMapping("/read")
    public String read() {
        String value = redisTemplate.opsForValue().get("web-key");
        return "读取到的值: " + value;
    }
}
